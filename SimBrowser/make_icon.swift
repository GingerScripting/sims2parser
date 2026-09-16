// Draws the Sim Studio app icon: a rounded tile with a shipping box, from
// an SF Symbol, at every size an .icns needs. Run by make_app.sh once:
//   swift make_icon.swift Resources/SimStudio.icns
import AppKit
import Foundation

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "SimStudio.icns")
let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("SimStudio-\(UUID().uuidString).iconset")
try! FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)

func draw(_ px: Int) -> NSImage {
    let size = NSSize(width: px, height: px)
    let image = NSImage(size: size)
    image.lockFocus()
    let inset = CGFloat(px) * 0.06
    let rect = NSRect(x: inset, y: inset, width: CGFloat(px) - 2 * inset, height: CGFloat(px) - 2 * inset)
    let path = NSBezierPath(roundedRect: rect, xRadius: rect.width * 0.22, yRadius: rect.width * 0.22)
    let gradient = NSGradient(colors: [NSColor(calibratedRed: 0.20, green: 0.60, blue: 0.40, alpha: 1),
                                       NSColor(calibratedRed: 0.09, green: 0.36, blue: 0.26, alpha: 1)])!
    gradient.draw(in: path, angle: -90)
    let config = NSImage.SymbolConfiguration(pointSize: CGFloat(px) * 0.5, weight: .medium)
    if let symbol = NSImage(systemSymbolName: "shippingbox.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(config) {
        let tinted = NSImage(size: symbol.size)
        tinted.lockFocus()
        symbol.draw(in: NSRect(origin: .zero, size: symbol.size))
        NSColor.white.set()
        NSRect(origin: .zero, size: symbol.size).fill(using: .sourceAtop)
        tinted.unlockFocus()
        let s = symbol.size
        tinted.draw(in: NSRect(x: (CGFloat(px) - s.width) / 2, y: (CGFloat(px) - s.height) / 2 + CGFloat(px) * 0.02,
                               width: s.width, height: s.height))
    }
    image.unlockFocus()
    return image
}

for (name, px) in [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
                   ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256),
                   ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024)] {
    let image = draw(px)
    guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { continue }
    try! png.write(to: tmp.appendingPathComponent(name + ".png"))
}
let task = Process()
task.launchPath = "/usr/bin/iconutil"
task.arguments = ["-c", "icns", tmp.path, "-o", out.path]
task.launch()
task.waitUntilExit()
try? FileManager.default.removeItem(at: tmp)
print(task.terminationStatus == 0 ? "wrote \(out.path)" : "iconutil failed")
