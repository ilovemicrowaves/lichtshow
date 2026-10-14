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

// 0. `open --stdout <fifo>` opent de fifo lezen+schrijven. Daardoor is de app
//    zijn eigen lezer: verdwijnt LedFx, dan komt er nooit EPIPE en blijft
//    write() hangen (gezien 14 okt). Daarom stdout opnieuw openen, alleen
//    schrijven. Is de lezer al weg, dan direct stoppen, vóór er iets bestaat.
var st = stat()
if fstat(1, &st) == 0 && (st.st_mode & S_IFMT) == S_IFIFO {
    var pad = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    if fcntl(1, F_GETPATH, &pad) == 0 {
        let fd = open(pad, O_WRONLY | O_NONBLOCK)
        if fd < 0 { exit(0) }  // geen lezer meer
        _ = fcntl(fd, F_SETFL, 0)  // weer blokkerend schrijven (in de schrijfthread)
        dup2(fd, 1)
        close(fd)
    }
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

// De buffers komen op de rate van het aggregate device (= die van de klok,
// het uitvoerapparaat), niet op die van de tap: de drift-compensatie zet de tap
// om. Op Joels MacBook-speakers is dat 44100, terwijl de tap 48000 meldt. Die
// rate melden gaf 8% te weinig blokken en een verschoven spectrum (14 okt).
let rate = prop(aggID, kAudioDevicePropertyNominalSampleRate, Float64(0))
FileHandle.standardError.write(
    "formaat: \(Int(rate)) Hz, \(format.mChannelsPerFrame) kanalen, float32\n".data(using: .utf8)!)

// 3. Opruimen gebeurt alleen op de main queue, één keer, in vaste volgorde
//    (stoppen → IO-proc weg → aggregate weg → tap weg). Nooit in een
//    signal handler en nooit vanuit de IO-thread zelf: dat kan Core Audio
//    laten vastlopen (review 14 okt; Joel zag Core Audio die dag hangen).
var procID: AudioDeviceIOProcID?
var opgeruimd = false
func stoppen() {
    if opgeruimd { return }
    opgeruimd = true
    if let id = procID {
        AudioDeviceStop(aggID, id)
        AudioDeviceDestroyIOProcID(aggID, id)
    }
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
    exit(0)
}
signal(SIGPIPE, SIG_IGN)
var signaalbronnen: [DispatchSourceSignal] = []
for s in [SIGINT, SIGTERM] {
    signal(s, SIG_IGN)
    let bron = DispatchSource.makeSignalSource(signal: s, queue: .main)
    bron.setEventHandler { stoppen() }
    bron.resume()
    signaalbronnen.append(bron)
}

// 4. De IO-thread (realtime) kopieert alleen naar een vooraf gereserveerde
//    ringbuffer; een gewone thread schrijft naar stdout. Loopt de lezer achter,
//    dan vallen er stukken weg in plaats van dat de audiothread blokkeert.
final class Ring {
    let cap: Int
    let buf: UnsafeMutablePointer<UInt8>
    var kop = 0, vulling = 0
    var slot = os_unfair_lock()
    init(cap: Int) { self.cap = cap; buf = .allocate(capacity: cap) }
    func erin(_ p: UnsafeRawPointer, _ n: Int) -> Bool {
        os_unfair_lock_lock(&slot); defer { os_unfair_lock_unlock(&slot) }
        if n > cap - vulling { return false }
        let src = p.assumingMemoryBound(to: UInt8.self)
        let staart = (kop + vulling) % cap
        let eerste = min(n, cap - staart)
        (buf + staart).update(from: src, count: eerste)
        if n > eerste { buf.update(from: src + eerste, count: n - eerste) }
        vulling += n
        return true
    }
    func eruit(_ doel: UnsafeMutablePointer<UInt8>, _ max: Int) -> Int {
        os_unfair_lock_lock(&slot); defer { os_unfair_lock_unlock(&slot) }
        let n = min(max, vulling)
        let eerste = min(n, cap - kop)
        doel.update(from: buf + kop, count: eerste)
        if n > eerste { (doel + eerste).update(from: buf, count: n - eerste) }
        kop = (kop + n) % cap
        vulling -= n
        return n
    }
}
let ring = Ring(cap: 4 * 1024 * 1024)  // ~12 s stereo float32 bij 44,1 kHz
let erIsData = DispatchSemaphore(value: 0)
let tapKanalen = format.mChannelsPerFrame

Thread {
    let blok = UnsafeMutablePointer<UInt8>.allocate(capacity: 65536)
    while true {
        erIsData.wait()
        while true {
            let n = ring.eruit(blok, 65536)
            if n == 0 { break }
            var gedaan = 0
            while gedaan < n {
                let w = write(1, blok + gedaan, n - gedaan)
                if w < 0 {
                    if errno == EINTR { continue }
                    DispatchQueue.main.async { stoppen() }  // lezer is weg
                    return
                }
                gedaan += w
            }
        }
    }
}.start()

let debug = ProcessInfo.processInfo.environment["TAP_DEBUG"] != nil
var aanroepen = 0
err = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, nil) { _, inData, _, _, _ in
    let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    aanroepen += 1
    if debug && aanroepen % 100 == 1 {
        let sizes = abl.map { "\($0.mNumberChannels)ch/\($0.mDataByteSize)B" }.joined(separator: ",")
        FileHandle.standardError.write("callback \(aanroepen): \(abl.count) buffers [\(sizes)]\n".data(using: .utf8)!)
    }
    // Alleen de tap: die komt ná de invoerkanalen van het uitvoerapparaat.
    // Heeft dat apparaat zelf ingangen (zoals de SSL 2), dan zitten die ervóór.
    guard let tap = abl.last, tap.mNumberChannels == tapKanalen,
          let p = tap.mData, tap.mDataByteSize > 0 else { return }
    if ring.erin(p, Int(tap.mDataByteSize)) { erIsData.signal() }
}
if err != noErr { fail("IO-proc mislukt", err) }
err = AudioDeviceStart(aggID, procID)
if err != noErr { fail("starten mislukt", err) }

dispatchMain()
