import SwiftUI

/// A ten-segment meter in the style of the game's own personality and skill
/// bars (and SimPE's): each cell is one point of a 0–1000 value, filled in
/// `tint`, partly filled for values between points. Clicking or dragging
/// across the cells sets the value to a whole number of points; an exact
/// value is typed into whatever number field sits beside it.
///
/// `signed` draws `segments` cells either side of a centre line, negatives
/// filling leftwards in `negativeTint` — the shape of a gender-preference
/// score, which runs both ways from zero.
public struct SegmentBar: View {
    @Binding var value: Int
    let segments: Int
    let unit: Int
    let signed: Bool
    let tint: Color
    let negativeTint: Color

    let segmentWidth: CGFloat = 13
    let height: CGFloat = 14
    let gap: CGFloat = 2

    public init(value: Binding<Int>, segments: Int = 10, unit: Int = 100, signed: Bool = false,
                tint: Color = .green, negativeTint: Color = .red) {
        _value = value
        self.segments = segments
        self.unit = unit
        self.signed = signed
        self.tint = tint
        self.negativeTint = negativeTint
    }

    private var cellCount: Int { signed ? segments * 2 : segments }
    private var pitch: CGFloat { segmentWidth + gap }
    private func isNegative(_ i: Int) -> Bool { signed && i < segments }

    /// How much of cell `i` is lit, 0…1. Positive cells count up from the
    /// centre (or the left edge); negative cells count down from the centre.
    private func fill(_ i: Int) -> CGFloat {
        let low: Int
        let magnitude: Int
        if isNegative(i) {
            low = (segments - 1 - i) * unit
            magnitude = -value
        } else {
            low = (signed ? i - segments : i) * unit
            magnitude = value
        }
        return CGFloat(min(1, max(0, Double(magnitude - low) / Double(unit))))
    }

    private func value(atX x: CGFloat) -> Int {
        let cells = Int((x / pitch).rounded())
        if signed {
            return min(segments, max(-segments, cells - segments)) * unit
        }
        return min(segments, max(0, cells)) * unit
    }

    public var body: some View {
        HStack(spacing: gap) {
            ForEach(0..<cellCount, id: \.self) { i in
                ZStack(alignment: isNegative(i) ? .trailing : .leading) {
                    RoundedRectangle(cornerRadius: 2).fill(.quaternary)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(isNegative(i) ? negativeTint : tint)
                        .frame(width: segmentWidth * fill(i))
                }
                .frame(width: segmentWidth, height: height)
            }
        }
        .overlay {
            if signed {
                Rectangle().fill(.secondary).frame(width: 1, height: height + 6)
            }
        }
        .contentShape(Rectangle())
        .gesture(DragGesture(minimumDistance: 0).onChanged { g in
            let v = value(atX: g.location.x)
            if v != value { value = v }
        })
        .help("\(value)")
        .accessibilityElement()
        .accessibilityValue("\(value)")
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: value = min(segments * unit, value + unit)
            case .decrement: value = max(signed ? -segments * unit : 0, value - unit)
            @unknown default: break
            }
        }
    }
}
