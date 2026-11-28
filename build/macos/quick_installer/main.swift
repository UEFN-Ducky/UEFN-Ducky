// UEFN Ducky Installer: the Mac's quick download (like the Windows web Setup).
//
// A small universal app. It asks the Store for the latest release, downloads this Mac's
// build (Apple Silicon or Intel) as the app zip, checks its SHA-256, unpacks it into
// Applications and opens it. Files an app downloads itself carry no quarantine flag, so
// only this installer needs "Open Anyway" once, never the 190 MB app.
//
//   "UEFN Ducky Installer"                          window with progress
//   "UEFN Ducky Installer" --headless [--dest DIR]  no window: prints "ok <version> <arch> <path>"
import Cocoa
import CryptoKit

let storeBase = ProcessInfo.processInfo.environment["DUCKY_STORE_URL"] ?? "https://uefnducky.org"
let appName = "UEFN Ducky.app"
// The Store answers 403 to unknown agents; this is the Windows web Setup's family name.
let userAgent = "UEFN-Ducky-Setup/1.0 (macOS quick installer)"

struct Failure: Error {
    let message: String
    init(_ message: String) { self.message = message }
}

func describe(_ error: Error) -> String {
    (error as? Failure)?.message ?? error.localizedDescription
}

/// The real chip: under Rosetta `uname` says x86_64, hw.optional.arm64 still says Apple Silicon.
func chip() -> String {
    var value: Int32 = 0
    var size = MemoryLayout<Int32>.size
    return sysctlbyname("hw.optional.arm64", &value, &size, nil, 0) == 0 && value == 1 ? "arm64" : "x64"
}

struct Release {
    let version: String
    let url: URL
    let sha256: String
}

func syncData(_ request: URLRequest) throws -> Data {
    var result: Result<Data, Error> = .failure(Failure("No answer from the UEFN Ducky Store."))
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: request) { data, response, error in
        if let error = error {
            result = .failure(error)
        } else if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            result = .failure(Failure("The UEFN Ducky Store answered \(http.statusCode). Try again later."))
        } else {
            result = .success(data ?? Data())
        }
        done.signal()
    }.resume()
    done.wait()
    return try result.get()
}

/// The feed row the in-app updater uses on this Mac: os=macos, arch=<chip>, package=app-zip.
func latestRelease(arch: String) throws -> Release {
    var request = URLRequest(url: URL(string: storeBase + "/api/v1/plugins/uefn-ducky-store/collect/app-version")!)
    request.httpMethod = "POST"
    request.timeoutInterval = 30
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue(storeBase, forHTTPHeaderField: "Origin")
    request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
    request.httpBody = Data("{}".utf8)
    let data: Data
    do {
        data = try syncData(request)
    } catch let error as Failure {
        throw error
    } catch {
        throw Failure("Can't reach the UEFN Ducky Store. Check your internet connection and try again.")
    }
    guard let doc = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw Failure("The UEFN Ducky Store sent an answer the installer can't read.")
    }
    let payload = (doc["payload"] as? [String: Any]) ?? doc
    if payload["downloadsPaused"] as? Bool == true {
        throw Failure("Downloads are paused right now. Try again later.")
    }
    let rows = payload["assets"] as? [[String: Any]] ?? []
    guard
        let row = rows.first(where: { $0["os"] as? String == "macos" && $0["arch"] as? String == arch && $0["package"] as? String == "app-zip" }),
        let link = row["url"] as? String,
        let sha = row["sha256"] as? String, sha.count == 64,
        let url = URL(string: link.hasPrefix("https://") ? link : storeBase + link),
        url.scheme == "https" || storeBase.hasPrefix("http://127.0.0.1")
    else {
        throw Failure("The latest release has no build for this Mac yet.")
    }
    return Release(version: payload["currentVersion"] as? String ?? "", url: url, sha256: sha.lowercased())
}

final class Downloader: NSObject, URLSessionDownloadDelegate {
    let target: URL
    let progress: (Double) -> Void
    var failure: Error?
    let done = DispatchSemaphore(value: 0)

    init(target: URL, progress: @escaping (Double) -> Void) {
        self.target = target
        self.progress = progress
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData _: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesExpectedToWrite > 0 {
            progress(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite))
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        if let http = downloadTask.response as? HTTPURLResponse, http.statusCode != 200 {
            failure = Failure("The download answered \(http.statusCode). Try again later.")
            return
        }
        // The temp file is gone once this returns: move it now.
        do {
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.moveItem(at: location, to: target)
        } catch {
            failure = error
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            failure = Failure("The download didn't finish. Check your internet connection and try again. (\(error.localizedDescription))")
        }
        done.signal()
    }
}

func download(_ url: URL, to target: URL, progress: @escaping (Double) -> Void) throws {
    let downloader = Downloader(target: target, progress: progress)
    let session = URLSession(configuration: .default, delegate: downloader, delegateQueue: nil)
    var request = URLRequest(url: url)
    request.timeoutInterval = 60
    request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
    session.downloadTask(with: request).resume()
    downloader.done.wait()
    session.finishTasksAndInvalidate()
    if let failure = downloader.failure { throw failure }
}

func sha256(of file: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: file)
    defer { try? handle.close() }
    var hasher = SHA256()
    while true {
        let chunk = handle.readData(ofLength: 1 << 20)
        if chunk.isEmpty { break }
        hasher.update(data: chunk)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}

func run(_ tool: String, _ args: [String]) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: tool)
    process.arguments = args
    try process.run()
    process.waitUntilExit()
    if process.terminationStatus != 0 {
        throw Failure("\(URL(fileURLWithPath: tool).lastPathComponent) failed (\(process.terminationStatus)).")
    }
}

/// /Applications when this user can write there (admins can), else ~/Applications.
func defaultDestination() -> URL {
    FileManager.default.isWritableFile(atPath: "/Applications")
        ? URL(fileURLWithPath: "/Applications")
        : FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
}

enum Step {
    case asking, downloading(Double), checking, installing
}

/// Downloads, checks and installs the latest release into `destination`; returns the app.
/// Nothing lands in `destination` before the checksum matched.
func install(into destination: URL, step: @escaping (Step) -> Void) throws -> (Release, URL) {
    step(.asking)
    let release = try latestRelease(arch: chip())
    let fm = FileManager.default
    let target = destination.appendingPathComponent(appName)
    if NSWorkspace.shared.runningApplications.contains(where: { $0.bundleURL?.standardizedFileURL == target.standardizedFileURL }) {
        throw Failure("UEFN Ducky is open. Quit it, then run the installer again.")
    }
    let work = fm.temporaryDirectory.appendingPathComponent("uefn-ducky-install-\(UUID().uuidString)")
    try fm.createDirectory(at: work, withIntermediateDirectories: true)
    defer { try? fm.removeItem(at: work) }
    let zip = work.appendingPathComponent("ducky.zip")
    step(.downloading(0))
    try download(release.url, to: zip) { step(.downloading($0)) }
    step(.checking)
    guard try sha256(of: zip) == release.sha256 else {
        throw Failure("The download doesn't match the Store's checksum. Try again.")
    }
    step(.installing)
    let unpacked = work.appendingPathComponent("unpacked")
    try run("/usr/bin/ditto", ["-x", "-k", zip.path, unpacked.path])
    let app = unpacked.appendingPathComponent(appName)
    guard fm.fileExists(atPath: app.appendingPathComponent("Contents/MacOS").path) else {
        throw Failure("The download has no UEFN Ducky app inside. Try again.")
    }
    try fm.createDirectory(at: destination, withIntermediateDirectories: true)
    if fm.fileExists(atPath: target.path) {
        try fm.removeItem(at: target)
    }
    try run("/usr/bin/ditto", [app.path, target.path])
    // ponytail: our own download has no quarantine flag; this only clears one a copy tool added.
    try? run("/usr/bin/xattr", ["-dr", "com.apple.quarantine", target.path])
    return (release, target)
}

// MARK: - Window

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let title = NSTextField(labelWithString: "Installing UEFN Ducky")
    let status = NSTextField(labelWithString: "Getting ready…")
    let bar = NSProgressIndicator()
    let button = NSButton(title: "Cancel", target: nil, action: nil)

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(appItem)
        NSApp.mainMenu = menu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 170),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "UEFN Ducky Installer"
        title.font = .systemFont(ofSize: 18, weight: .semibold)
        status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byWordWrapping
        status.maximumNumberOfLines = 2
        bar.isIndeterminate = true
        bar.minValue = 0
        bar.maxValue = 1
        bar.startAnimation(nil)
        button.target = self
        button.action = #selector(quit)
        button.bezelStyle = .rounded
        let stack = NSStackView(views: [title, status, bar, button])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 22, left: 24, bottom: 20, right: 24)
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = NSView()
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            bar.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -48),
            status.widthAnchor.constraint(equalTo: bar.widthAnchor),
        ])
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        DispatchQueue.global(qos: .userInitiated).async { self.work() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    @objc func quit() { NSApp.terminate(nil) }

    func show(_ step: Step) {
        DispatchQueue.main.async {
            switch step {
            case .asking:
                self.status.stringValue = "Finding the latest release for your Mac…"
            case .downloading(let done):
                if self.bar.isIndeterminate && done > 0 {
                    self.bar.isIndeterminate = false
                    self.bar.stopAnimation(nil)
                }
                self.bar.doubleValue = done
                self.status.stringValue = "Downloading UEFN Ducky (\(chip() == "arm64" ? "Apple Silicon" : "Intel"))… \(Int(done * 100))%"
            case .checking:
                self.status.stringValue = "Checking the download…"
            case .installing:
                self.bar.isIndeterminate = true
                self.bar.startAnimation(nil)
                self.status.stringValue = "Installing into Applications…"
            }
        }
    }

    func work() {
        do {
            let (release, app) = try install(into: defaultDestination(), step: show)
            DispatchQueue.main.async {
                self.bar.isIndeterminate = false
                self.bar.stopAnimation(nil)
                self.bar.doubleValue = 1
                self.title.stringValue = "UEFN Ducky \(release.version) is installed"
                self.status.stringValue = "Opening it now. You can delete this installer."
                self.button.title = "Done"
                NSWorkspace.shared.openApplication(at: app, configuration: NSWorkspace.OpenConfiguration()) { _, _ in
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { NSApp.terminate(nil) }
                }
            }
        } catch {
            DispatchQueue.main.async {
                self.bar.stopAnimation(nil)
                self.bar.isHidden = true
                self.title.stringValue = "Couldn't install UEFN Ducky"
                self.status.textColor = .systemRed
                self.status.stringValue = describe(error)
                self.button.title = "Quit"
            }
        }
    }
}

let arguments = CommandLine.arguments
if arguments.contains("--headless") {
    var destination = defaultDestination()
    if let i = arguments.firstIndex(of: "--dest"), i + 1 < arguments.count {
        destination = URL(fileURLWithPath: arguments[i + 1])
    }
    do {
        let (release, app) = try install(into: destination) { _ in }
        print("ok \(release.version) \(chip()) \(app.path)")
        exit(0)
    } catch {
        print("failed: \(describe(error))")
        exit(1)
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
