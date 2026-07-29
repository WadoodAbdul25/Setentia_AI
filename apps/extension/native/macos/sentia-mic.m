#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>
#import <signal.h>
#import <unistd.h>

static const double SentiaSampleRate = 16000.0;

static void fail(NSString *message) {
  NSString *line = [NSString stringWithFormat:@"ERROR: %@\n", message];
  write(STDERR_FILENO, line.UTF8String, strlen(line.UTF8String));
  exit(1);
}

static BOOL microphoneAllowed(void) {
  AVAuthorizationStatus status =
      [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
  if (status == AVAuthorizationStatusAuthorized) {
    return YES;
  }
  if (status == AVAuthorizationStatusDenied ||
      status == AVAuthorizationStatusRestricted) {
    return NO;
  }

  dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
  __block BOOL allowed = NO;
  [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                          completionHandler:^(BOOL granted) {
                            allowed = granted;
                            dispatch_semaphore_signal(semaphore);
                          }];
  dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
  return allowed;
}

@interface SentiaMicrophoneRecorder : NSObject
@property(nonatomic, strong) AVAudioEngine *engine;
@property(nonatomic, strong) AVAudioConverter *converter;
- (BOOL)start:(NSError **)error;
@end

@implementation SentiaMicrophoneRecorder

- (instancetype)init {
  self = [super init];
  if (self) {
    _engine = [[AVAudioEngine alloc] init];
  }
  return self;
}

- (BOOL)start:(NSError **)error {
  AVAudioInputNode *input = self.engine.inputNode;
  AVAudioFormat *sourceFormat = [input outputFormatForBus:0];
  if (sourceFormat.sampleRate <= 0 || sourceFormat.channelCount == 0) {
    if (error) {
      *error = [NSError
          errorWithDomain:@"SentiaMicrophone"
                     code:1
                 userInfo:@{
                   NSLocalizedDescriptionKey : @"No audio input device is available."
                 }];
    }
    return NO;
  }

  AVAudioFormat *targetFormat = [[AVAudioFormat alloc]
      initWithCommonFormat:AVAudioPCMFormatInt16
                sampleRate:SentiaSampleRate
                  channels:1
               interleaved:YES];
  self.converter =
      [[AVAudioConverter alloc] initFromFormat:sourceFormat toFormat:targetFormat];
  if (!targetFormat || !self.converter) {
    if (error) {
      *error = [NSError
          errorWithDomain:@"SentiaMicrophone"
                     code:2
                 userInfo:@{
                   NSLocalizedDescriptionKey :
                       @"Could not configure microphone audio conversion."
                 }];
    }
    return NO;
  }

  AVAudioConverter *converter = self.converter;
  [input installTapOnBus:0
              bufferSize:2048
                  format:sourceFormat
                   block:^(AVAudioPCMBuffer *buffer, AVAudioTime *when) {
                     AVAudioFrameCount capacity = (AVAudioFrameCount)ceil(
                         buffer.frameLength * SentiaSampleRate /
                         sourceFormat.sampleRate);
                     if (capacity == 0) {
                       return;
                     }
                     AVAudioPCMBuffer *converted = [[AVAudioPCMBuffer alloc]
                         initWithPCMFormat:targetFormat
                             frameCapacity:capacity];
                     __block BOOL supplied = NO;
                     NSError *conversionError = nil;
                     AVAudioConverterOutputStatus conversionStatus =
                         [converter
                             convertToBuffer:converted
                                       error:&conversionError
                          withInputFromBlock:^AVAudioBuffer *(
                              AVAudioPacketCount packetCount,
                              AVAudioConverterInputStatus *inputStatus) {
                            if (supplied) {
                              *inputStatus = AVAudioConverterInputStatus_NoDataNow;
                              return nil;
                            }
                            supplied = YES;
                            *inputStatus = AVAudioConverterInputStatus_HaveData;
                            return buffer;
                          }];
                     if (conversionStatus == AVAudioConverterOutputStatus_Error ||
                         conversionError || converted.frameLength == 0) {
                       return;
                     }
                     AudioBuffer audio = converted.audioBufferList->mBuffers[0];
                     if (audio.mData && audio.mDataByteSize > 0) {
                       write(STDOUT_FILENO, audio.mData, audio.mDataByteSize);
                     }
                   }];

  [self.engine prepare];
  return [self.engine startAndReturnError:error];
}

@end

static void stopRecorder(int signalNumber) { exit(0); }

int main(void) {
  @autoreleasepool {
    if (!microphoneAllowed()) {
      fail(@"Microphone access was denied. Allow Sentia Microphone in System "
           @"Settings > Privacy & Security > Microphone.");
    }

    SentiaMicrophoneRecorder *recorder = [[SentiaMicrophoneRecorder alloc] init];
    NSError *error = nil;
    if (![recorder start:&error]) {
      fail(error.localizedDescription ?: @"Could not start microphone capture.");
    }

    signal(SIGINT, stopRecorder);
    signal(SIGTERM, stopRecorder);
    const char *ready = "READY: Capturing 16 kHz mono PCM.\n";
    write(STDERR_FILENO, ready, strlen(ready));
    [[NSRunLoop mainRunLoop] run];
  }
  return 0;
}
