#import <AudioToolbox/AudioToolbox.h>
#import <Foundation/Foundation.h>
#import <signal.h>
#import <stdatomic.h>
#import <stdbool.h>
#import <unistd.h>

static const double SentiaOutputSampleRate = 24000.0;
static const UInt32 SentiaPrebufferFrames = 2400;

typedef struct {
  dispatch_group_t playback;
  _Atomic(UInt64) queuedFrames;
  _Atomic(bool) playing;
  _Atomic(bool) inputFinished;
  _Atomic(UInt32) underruns;
} SentiaPlaybackContext;

static void fail(NSString *message) {
  NSString *line = [NSString stringWithFormat:@"ERROR: %@\n", message];
  write(STDERR_FILENO, line.UTF8String, strlen(line.UTF8String));
  exit(1);
}

static void failStatus(NSString *operation, OSStatus status) {
  fail([NSString stringWithFormat:@"%@ failed with audio status %d.",
                                   operation, (int)status]);
}

static void stopPlayback(int signalNumber) { exit(0); }

static void audioBufferFinished(void *userData, AudioQueueRef queue,
                                AudioQueueBufferRef buffer) {
  SentiaPlaybackContext *context = (SentiaPlaybackContext *)userData;
  UInt64 frames = buffer->mAudioDataByteSize / sizeof(int16_t);
  atomic_fetch_sub(&context->queuedFrames, frames);
  AudioQueueFreeBuffer(queue, buffer);
  dispatch_group_leave(context->playback);
}

static void audioQueueRunningChanged(void *userData, AudioQueueRef queue,
                                     AudioQueuePropertyID propertyID) {
  SentiaPlaybackContext *context = (SentiaPlaybackContext *)userData;
  UInt32 running = 0;
  UInt32 size = sizeof(running);
  if (AudioQueueGetProperty(queue, kAudioQueueProperty_IsRunning, &running,
                            &size) != noErr) {
    return;
  }
  if (running == 0 && atomic_exchange(&context->playing, false) &&
      !atomic_load(&context->inputFinished)) {
    atomic_fetch_add(&context->underruns, 1);
  }
}

int main(void) {
  @autoreleasepool {
    signal(SIGINT, stopPlayback);
    signal(SIGTERM, stopPlayback);

    AudioStreamBasicDescription format = {0};
    format.mSampleRate = SentiaOutputSampleRate;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags =
        kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked;
    format.mBytesPerPacket = sizeof(int16_t);
    format.mFramesPerPacket = 1;
    format.mBytesPerFrame = sizeof(int16_t);
    format.mChannelsPerFrame = 1;
    format.mBitsPerChannel = 16;

    SentiaPlaybackContext context = {
        .playback = dispatch_group_create(),
        .queuedFrames = 0,
        .playing = false,
        .inputFinished = false,
        .underruns = 0,
    };
    AudioQueueRef queue = NULL;
    OSStatus status = AudioQueueNewOutput(&format, audioBufferFinished,
                                          &context, NULL, NULL, 0, &queue);
    if (status != noErr) {
      failStatus(@"Starting the output device", status);
    }
    status = AudioQueueAddPropertyListener(
        queue, kAudioQueueProperty_IsRunning, audioQueueRunningChanged,
        &context);
    if (status != noErr) {
      failStatus(@"Monitoring the output device", status);
    }

    NSMutableData *pending = [NSMutableData data];
    uint8_t incoming[8192];
    UInt64 totalFrames = 0;

    while (true) {
      ssize_t count = read(STDIN_FILENO, incoming, sizeof(incoming));
      if (count < 0) {
        fail(@"Could not read synthesized audio.");
      }
      if (count == 0) {
        break;
      }
      [pending appendBytes:incoming length:(NSUInteger)count];
      UInt32 usableBytes =
          (UInt32)(pending.length - (pending.length % sizeof(int16_t)));
      if (usableBytes == 0) {
        continue;
      }

      AudioQueueBufferRef buffer = NULL;
      status = AudioQueueAllocateBuffer(queue, usableBytes, &buffer);
      if (status != noErr) {
        failStatus(@"Allocating an audio buffer", status);
      }
      memcpy(buffer->mAudioData, pending.bytes, usableBytes);
      buffer->mAudioDataByteSize = usableBytes;
      dispatch_group_enter(context.playback);
      status = AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
      if (status != noErr) {
        dispatch_group_leave(context.playback);
        AudioQueueFreeBuffer(queue, buffer);
        failStatus(@"Queueing synthesized audio", status);
      }
      UInt64 bufferFrames = usableBytes / sizeof(int16_t);
      atomic_fetch_add(&context.queuedFrames, bufferFrames);
      totalFrames += bufferFrames;
      [pending replaceBytesInRange:NSMakeRange(0, usableBytes)
                        withBytes:NULL
                           length:0];

      if (!atomic_load(&context.playing) &&
          atomic_load(&context.queuedFrames) >= SentiaPrebufferFrames) {
        status = AudioQueueStart(queue, NULL);
        if (status != noErr) {
          failStatus(@"Playing synthesized audio", status);
        }
        atomic_store(&context.playing, true);
        dprintf(STDERR_FILENO,
                "[metric] playback_started prebuffer_frames=%u "
                "prebuffer_ms=%.1f\n",
                SentiaPrebufferFrames,
                SentiaPrebufferFrames * 1000.0 / SentiaOutputSampleRate);
      }
    }

    atomic_store(&context.inputFinished, true);
    if (!atomic_load(&context.playing) &&
        atomic_load(&context.queuedFrames) > 0) {
      status = AudioQueueStart(queue, NULL);
      if (status != noErr) {
        failStatus(@"Playing synthesized audio", status);
      }
      atomic_store(&context.playing, true);
    }
    dispatch_group_wait(context.playback, DISPATCH_TIME_FOREVER);
    dprintf(STDERR_FILENO,
            "[metric] playback_finished total_frames=%llu duration_ms=%.1f "
            "underruns=%u\n",
            totalFrames, totalFrames * 1000.0 / SentiaOutputSampleRate,
            atomic_load(&context.underruns));
    AudioQueueStop(queue, true);
    AudioQueueRemovePropertyListener(
        queue, kAudioQueueProperty_IsRunning, audioQueueRunningChanged,
        &context);
    AudioQueueDispose(queue, true);
  }
  return 0;
}
