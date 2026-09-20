import Foundation
import AppKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

/// Turns any picture the Mac can open into what the daemon's texture code
/// reads: an 8-bit, non-interlaced RGBA PNG at the base texture's size.
/// Resizing happens here, with CoreGraphics, because the game's texture
/// coordinates assume the original layout; a picture of another shape is
/// stretched to fit rather than cropped.
public enum ImageNormalizer {
    public static func rgbaPNG(from url: URL, width: Int, height: Int) -> Data? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCache: false] as CFDictionary)
        else { return nil }
        return rgbaPNG(from: image, width: width, height: height)
    }

    public static func rgbaPNG(from image: CGImage, width: Int, height: Int) -> Data? {
        guard width > 0, height > 0 else { return nil }
        let space = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        // Premultiplied is the only RGBA layout CGContext draws into; the
        // alpha is divided back out below so the PNG carries straight colour.
        guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                  bytesPerRow: width * 4, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        ctx.interpolationQuality = .high
        ctx.clear(CGRect(x: 0, y: 0, width: width, height: height))
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        guard let raw = ctx.data else { return nil }
        let px = raw.bindMemory(to: UInt8.self, capacity: width * height * 4)
        var i = 0
        while i < width * height * 4 {
            let a = Int(px[i + 3])
            if a > 0 && a < 255 {
                px[i] = UInt8(min(255, Int(px[i]) * 255 / a))
                px[i + 1] = UInt8(min(255, Int(px[i + 1]) * 255 / a))
                px[i + 2] = UInt8(min(255, Int(px[i + 2]) * 255 / a))
            }
            i += 4
        }
        guard let provider = CGDataProvider(data: Data(bytes: px, count: width * height * 4) as CFData),
              let straight = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                                     bytesPerRow: width * 4, space: space,
                                     bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                                     provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { return nil }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, straight, [kCGImagePropertyPNGInterlaceType: 0] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }
}
