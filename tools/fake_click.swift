// fake_click.swift — impersonate a Zwift Click v2 (left puck) so the real Zwift
// app talks to *this* process instead of the hardware. We replay the device-side
// frames captured from the real puck (RideOn reply, log, vendor status, the
// `ff 03` auth challenge) and log every byte Zwift writes to 0003.
//
//   swiftc -O -o .build/fake_click tools/fake_click.swift -framework CoreBluetooth
//   .build/fake_click <challenge-hex> [run-seconds] [logfile]
//
// The challenge is the device->host `ff 03 ...` notification, header included.
import Foundation
import CoreBluetooth

let args = CommandLine.arguments
guard args.count > 1 else { print("usage: fake_click <challenge-hex> [secs] [log]"); exit(2) }
func hexToData(_ s: String) -> Data {
    var out = [UInt8](); var i = s.startIndex
    while i < s.endIndex {
        let j = s.index(i, offsetBy: 2, limitedBy: s.endIndex) ?? s.endIndex
        if let b = UInt8(s[i..<j], radix: 16) { out.append(b) }
        i = j
    }
    return Data(out)
}
let challenge = hexToData(args[1].replacingOccurrences(of: " ", with: ""))
let runSeconds = args.count > 2 ? (Double(args[2]) ?? 300) : 300
let logPath = args.count > 3 ? args[3] : "/tmp/fake_click.log"

let logQueue = DispatchQueue(label: "log")
var logHandle: FileHandle? = {
    FileManager.default.createFile(atPath: logPath, contents: nil)
    return FileHandle(forWritingAtPath: logPath)
}()
let t0 = Date()
func log(_ msg: String) {
    let line = String(format: "[%7.2f] %@\n", Date().timeIntervalSince(t0), msg)
    logQueue.sync {
        logHandle?.write(line.data(using: .utf8)!)
        FileHandle.standardError.write(line.data(using: .utf8)!)
    }
}

let SVC_FC82 = CBUUID(string: "0000FC82-0000-1000-8000-00805F9B34FB")
let CH_ASYNC = CBUUID(string: "00000002-19CA-4651-86E5-FA29DCDD09D1")  // notify
let CH_SYNC_RX = CBUUID(string: "00000003-19CA-4651-86E5-FA29DCDD09D1") // write no rsp
let CH_SYNC_TX = CBUUID(string: "00000004-19CA-4651-86E5-FA29DCDD09D1") // read + indicate

final class FakeClick: NSObject, CBPeripheralManagerDelegate {
    var pm: CBPeripheralManager!
    var async_ = CBMutableCharacteristic(type: CH_ASYNC, properties: [.notify], value: nil, permissions: [.readable])
    var rx = CBMutableCharacteristic(type: CH_SYNC_RX, properties: [.writeWithoutResponse], value: nil, permissions: [.writeable])
    var tx = CBMutableCharacteristic(type: CH_SYNC_TX, properties: [.read, .indicate], value: nil, permissions: [.readable])
    var replayDone = false
    var staticValues: [CBUUID: Data] = [:]

    func start() {
        pm = CBPeripheralManager(delegate: self, queue: nil)
    }

    func peripheralManagerDidUpdateState(_ p: CBPeripheralManager) {
        log("state \(p.state.rawValue)")
        guard p.state == .poweredOn else { return }
        let di = CBMutableService(type: CBUUID(string: "180A"), primary: true)
        // Cached values on a characteristic must be read-only, so every value is
        // served dynamically from staticValues in didReceiveRead.
        func str(_ uuid: String, _ v: String) -> CBMutableCharacteristic {
            let u = CBUUID(string: uuid)
            staticValues[u] = v.data(using: .utf8)
            return CBMutableCharacteristic(type: u, properties: [.read], value: nil, permissions: [.readable])
        }
        di.characteristics = [
            str("2A29", "Zwift Inc"), str("2A25", "0B-D852FAA92D7E"),
            str("2A27", "B.0"), str("2A26", "1.2.0"),
        ]
        let bat = CBMutableService(type: CBUUID(string: "180F"), primary: true)
        staticValues[CBUUID(string: "2A19")] = Data([0x50])
        bat.characteristics = [CBMutableCharacteristic(type: CBUUID(string: "2A19"), properties: [.read, .notify],
                                                      value: nil, permissions: [.readable])]
        let fc = CBMutableService(type: SVC_FC82, primary: true)
        fc.characteristics = [async_, rx, tx]
        for s in [di, bat, fc] { p.add(s) }
        let advMode = ProcessInfo.processInfo.environment["FAKE_ADV"] ?? "all"
        var adv: [String: Any] = [CBAdvertisementDataIsConnectable: true as NSNumber]
        if advMode == "name" || advMode == "both" || advMode == "all" { adv[CBAdvertisementDataLocalNameKey] = "Zwift Click" }
        if advMode == "mfr" || advMode == "both" || advMode == "all" { adv[CBAdvertisementDataManufacturerDataKey] = Data([0x4A, 0x09, 0x0B, 0x2D, 0x7E]) }
        if advMode == "uuid" || advMode == "all" { adv[CBAdvertisementDataServiceUUIDsKey] = [SVC_FC82] }
        p.startAdvertising(adv)
        log("advertising as Zwift Click (mfr 094a 0b2d7e), challenge \(challenge.count) B")
    }

    func peripheralManagerDidStartAdvertising(_ p: CBPeripheralManager, error: Error?) {
        log("startAdvertising -> \(error.map { "\($0)" } ?? "ok")")
    }

    func peripheralManager(_ p: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let e = error { log("add \(service.uuid) error \(e)") }
    }

    func peripheralManager(_ p: CBPeripheralManager, didReceiveRead req: CBATTRequest) {
        let u = req.characteristic.uuid
        let v = staticValues[u] ?? Data()
        log("READ  \(u.uuidString.prefix(8)) -> \(v.count) B")
        req.value = v
        p.respond(to: req, withResult: .success)
    }

    func peripheralManager(_ p: CBPeripheralManager, didReceiveWrite reqs: [CBATTRequest]) {
        for r in reqs {
            let hex = (r.value ?? Data()).map { String(format: "%02x", $0) }.joined()
            log("WRITE \(r.characteristic.uuid.uuidString.prefix(8)) \(hex)")
            if r.characteristic.uuid == CH_SYNC_RX && !replayDone { replay() }
        }
        p.respond(to: reqs[0], withResult: .success)
    }

    func peripheralManager(_ p: CBPeripheralManager, central: CBCentral, didSubscribeTo ch: CBCharacteristic) {
        log("SUBSCRIBE \(ch.uuid.uuidString.prefix(8)) from \(central.identifier.uuidString.prefix(8))")
    }

    func peripheralManager(_ p: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom ch: CBCharacteristic) {
        log("UNSUBSCRIBE \(ch.uuid.uuidString.prefix(8))")
    }

    func send(_ ch: CBMutableCharacteristic, _ data: Data, _ label: String) {
        let ok = pm.updateValue(data, for: ch, onSubscribedCentrals: nil)
        let hex = data.map { String(format: "%02x", $0) }.joined()
        log("SEND  \(ch.uuid.uuidString.prefix(8)) \(label) \(ok ? "" : "(queued) ")\(hex.prefix(80))")
    }

    // Everything below is the transcript the real left puck produced (probe2/probe5),
    // replayed so Zwift believes it is talking to hardware.
    func replay() {
        replayDone = true
        log("--- replaying device transcript ---")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            self.send(self.tx, Data("RideOn".utf8), "RideOn-reply")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
            self.send(self.async_, hexToData("2308ffffffff0f"), "buttons-all-released")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.60) {
            self.send(self.async_, hexToData("2a0803121212100818100018482205ffffffff1f28fb01"), "log")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.80) {
            self.send(self.async_, hexToData("ff0500fa05180a0c4438353246414139324437452050285030811738ea16"), "vendor-status")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.00) {
            self.send(self.async_, self.challengeForSend(), "AUTH-CHALLENGE")
        }
        // keep the link alive: battery heartbeat every 5 s, released buttons every 0.5 s
        for i in 1...600 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5 * Double(i)) {
                self.send(self.async_, hexToData("2308ffffffff0f"), "buttons")
            }
        }
        for i in 1...120 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 5.0 * Double(i)) {
                self.send(self.async_, hexToData("191050"), "battery")
            }
        }
    }

    func challengeForSend() -> Data { challenge }
}

let fake = FakeClick()
fake.start()
DispatchQueue.main.asyncAfter(deadline: .now() + runSeconds) {
    log("done")
    exit(0)
}
RunLoop.main.run()
