#import <AudioToolbox/AudioToolbox.h>
#import <Foundation/Foundation.h>
#import <signal.h>
#import <unistd.h>

static const double SentiaOutputSampleRate = 24000.0;
static const UInt32 SentiaPrebufferFrames = 2400;

typedef struct {
  dispatch_group_t playback;
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
  AudioQueueFreeBuffer(queue, buffer);
  dispatch_group_leave(context->playback);
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
    };
    AudioQueueRef queue = NULL;
    OSStatus status = AudioQueueNewOutput(&format, audioBufferFinished,
                                          &context, NULL, NULL, 0, &queue);
    if (status != noErr) {
      failStatus(@"Starting the output device", status);
    }

    NSMutableData *pending = [NSMutableData data];
    uint8_t incoming[8192];
    UInt32 scheduledFrames = 0;
    BOOL playing = NO;

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
      scheduledFrames += usableBytes / sizeof(int16_t);
      [pending replaceBytesInRange:NSMakeRange(0, usableBytes)
                        withBytes:NULL
                           length:0];

      if (!playing && scheduledFrames >= SentiaPrebufferFrames) {
        status = AudioQueueStart(queue, NULL);
        if (status != noErr) {
          failStatus(@"Playing synthesized audio", status);
        }
        playing = YES;
      }
    }

    if (!playing && scheduledFrames > 0) {
      status = AudioQueueStart(queue, NULL);
      if (status != noErr) {
        failStatus(@"Playing synthesized audio", status);
      }
    }
    dispatch_group_wait(context.playback, DISPATCH_TIME_FOREVER);
    AudioQueueStop(queue, true);
    AudioQueueDispose(queue, true);
  }
  return 0;
}
