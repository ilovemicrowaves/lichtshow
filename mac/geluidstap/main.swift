// Geluidstap: tapt het systeemgeluid van de Mac af via een Core Audio
// process tap (macOS 14.2+), zonder BlackHole of ander stuurprogramma.
// Schrijft ruwe audio naar stdout: float32, interleaved, in het formaat van
// de tap (meestal 2 kanalen, 48000 Hz). Op stderr staat vóór de eerste audio
// de regel "formaat: <rate> Hz, <n> kanalen, float32".
// Het geluid zelf blijft hoorbaar (muteBehavior = unmuted).
//
// Bouwen: mac/geluidstap/build.sh  (zet de app in ~/Applications)
// Starten: altijd als app via `open` (zie ledfx/lichtshow/geluidstap.py),
// niet los vanuit een shell. macOS geeft "systeemaudio opnemen" alleen aan
// een app met NSAudioCaptureUsageDescription; los gestart komen er stil geen
// buffers. Na opnieuw bouwen kan macOS de toestemming opnieuw vragen.
import CoreAudio
import AudioToolbox
import Foundation

func fail(_ msg: String, _ err: OSStatus) -> Never {
    FileHandle.standardError.write("geluidstap: \(msg) (\(err))\n".data(using: .utf8)!)
    exit(1)
}

func prop<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ initial: T) -> T {
    var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    let err = AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &value)
    if err != noErr { fail("property \(sel)", err) }
    return value
}

// 1. Tap op alle processen (globaal), geluid blijft hoorbaar. Uitgesloten:
//    Rogue Amoeba (SoundSource's arkaudiod speelt het geluid mét Joels EQ
//    opnieuw af). De analyse moet het schone signaal krijgen (eis Joel, 14 okt).
func audioProcessen() -> [AudioObjectID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                          mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids
}
let uitgesloten = audioProcessen().filter { id in
    var addr = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID,
                                          mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var bid = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &bid) == noErr else { return false }
    return (bid as String).hasPrefix("com.rogueamoeba.")
}
FileHandle.standardError.write("uitgesloten: \(uitgesloten.count) Rogue Amoeba-proces(sen)\n".data(using: .utf8)!)
let desc = CATapDescription(stereoGlobalTapButExcludeProcesses: uitgesloten)
desc.uuid = UUID()
desc.muteBehavior = .unmuted
desc.isPrivate = true
desc.name = "Lichtshow"
var tapID = AudioObjectID(kAudioObjectUnknown)
var err = AudioHardwareCreateProcessTap(desc, &tapID)
if err != noErr { fail("tap maken mislukt", err) }

var format = prop(tapID, kAudioTapPropertyFormat, AudioStreamBasicDescription())

// 2. Privé aggregate device met de tap erin. De standaarduitvoer zit erin als
//    klok: zonder echt apparaat kwamen er geen buffers (gezien 14 okt).
let outID = prop(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice,
                 AudioObjectID(kAudioObjectUnknown))
let outUID = prop(outID, kAudioDevicePropertyDeviceUID, "" as CFString) as String
FileHandle.standardError.write("klok: uitvoerapparaat \(outUID)\n".data(using: .utf8)!)
let aggUID = UUID().uuidString
let aggDesc: [String: Any] = [
    kAudioAggregateDeviceNameKey: "Lichtshow-tap",
    kAudioAggregateDeviceUIDKey: aggUID,
    kAudioAggregateDeviceMainSubDeviceKey: outUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outUID]],
    kAudioAggregateDeviceTapListKey: [[
        kAudioSubTapDriftCompensationKey: true,
        kAudioSubTapUIDKey: desc.uuid.uuidString,
    ]],
]
var aggID = AudioObjectID(kAudioObjectUnknown)
err = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggID)
if err != noErr { fail("aggregate device mislukt", err) }

FileHandle.standardError.write(
    "formaat: \(Int(format.mSampleRate)) Hz, \(format.mChannelsPerFrame) kanalen, float32\n".data(using: .utf8)!)

func opruimen() {
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
}
signal(SIGPIPE, SIG_IGN)
for s in [SIGINT, SIGTERM] {
    signal(s) { _ in opruimen(); exit(0) }
}

// 3. Elke buffer direct naar stdout.
let out = FileHandle.standardOutput
var procID: AudioDeviceIOProcID?
let debug = ProcessInfo.processInfo.environment["TAP_DEBUG"] != nil
var aanroepen = 0
err = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, nil) { _, inData, _, _, _ in
    let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    aanroepen += 1
    if debug && aanroepen % 100 == 1 {
        let sizes = abl.map { "\($0.mNumberChannels)ch/\($0.mDataByteSize)B" }.joined(separator: ",")
        FileHandle.standardError.write("callback \(aanroepen): \(abl.count) buffers [\(sizes)]\n".data(using: .utf8)!)
    }
    for buf in abl {
        guard let p = buf.mData, buf.mDataByteSize > 0 else { continue }
        let data = Data(bytes: p, count: Int(buf.mDataByteSize))
        do { try out.write(contentsOf: data) } catch { opruimen(); exit(0) }  // lezer weg
    }
}
if err != noErr { fail("IO-proc mislukt", err) }
err = AudioDeviceStart(aggID, procID)
if err != noErr { fail("starten mislukt", err) }

dispatchMain()
