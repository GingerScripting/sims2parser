import SwiftUI

/// Rolls what the game asks you for when a child becomes a teen: one Aspiration,
/// two Turn Ons and one Turn Off.
///
/// The trait list is transcribed from the printed guides rather than memory —
/// Nightlife ch. 4 "Turn Ons and Turn Offs" (19 traits) plus the 14 added by
/// Bon Voyage ch. 1. Later packs add none; Apartment Life's "witchiness" is a
/// potion effect, not an attraction trait.
enum Attraction {

    enum Pack: String {
        case nightlife = "Nightlife"
        case bonVoyage = "Bon Voyage"
    }

    struct Trait: Identifiable, Hashable {
        let name: String
        let pack: Pack
        var id: String { name }
    }

    /// The six a teen can choose at age-up. Grilled Cheese is deliberately
    /// absent: it is unlocked through a want, never offered on the age-up panel.
    static let aspirations = ["Romance", "Family", "Fortune",
                              "Popularity", "Knowledge", "Pleasure"]

    static let traits: [Trait] = [
        // Nightlife
        .init(name: "Black Hair", pack: .nightlife),
        .init(name: "Blonde Hair", pack: .nightlife),
        .init(name: "Brown Hair", pack: .nightlife),
        .init(name: "Red Hair", pack: .nightlife),
        .init(name: "Gray Hair", pack: .nightlife),
        .init(name: "Custom Hair", pack: .nightlife),
        .init(name: "Cologne", pack: .nightlife),
        .init(name: "Stink", pack: .nightlife),
        .init(name: "Makeup", pack: .nightlife),
        .init(name: "Full Face Makeup", pack: .nightlife),
        .init(name: "Glasses", pack: .nightlife),
        .init(name: "Hats", pack: .nightlife),
        .init(name: "Facial Hair", pack: .nightlife),
        .init(name: "Fatness", pack: .nightlife),
        .init(name: "Fitness", pack: .nightlife),
        .init(name: "Formalwear", pack: .nightlife),
        .init(name: "Swimwear", pack: .nightlife),
        .init(name: "Underwear", pack: .nightlife),
        .init(name: "Vampirism", pack: .nightlife),
        // Bon Voyage
        .init(name: "Athletic", pack: .bonVoyage),
        .init(name: "Logical", pack: .bonVoyage),
        .init(name: "Charismatic", pack: .bonVoyage),
        .init(name: "Creative", pack: .bonVoyage),
        .init(name: "Mechanical", pack: .bonVoyage),
        .init(name: "Great Cook", pack: .bonVoyage),
        .init(name: "Good at Cleaning", pack: .bonVoyage),
        .init(name: "Hard Worker", pack: .bonVoyage),
        .init(name: "Unemployed", pack: .bonVoyage),
        .init(name: "Jewelry", pack: .bonVoyage),
        .init(name: "Lycanthropy", pack: .bonVoyage),
        .init(name: "Plantsimism", pack: .bonVoyage),
        .init(name: "Zombiism", pack: .bonVoyage),
        .init(name: "Robots", pack: .bonVoyage),
    ]

    /// A sim's three attraction slots. The game never repeats a trait across
    /// them, so neither do we.
    struct Roll {
        var aspiration: String
        var turnOn1: Trait
        var turnOn2: Trait
        var turnOff: Trait

        var taken: [Trait] { [turnOn1, turnOn2, turnOff] }
    }

    static func roll() -> Roll {
        var pool = traits.shuffled()
        return Roll(aspiration: aspirations.randomElement()!,
                    turnOn1: pool.removeLast(),
                    turnOn2: pool.removeLast(),
                    turnOff: pool.removeLast())
    }

    /// Re-roll one slot, avoiding whatever the other two already hold.
    static func reroll(_ roll: Roll, slot: Slot) -> Roll {
        var r = roll
        switch slot {
        case .aspiration:
            r.aspiration = aspirations.filter { $0 != roll.aspiration }.randomElement() ?? roll.aspiration
        case .turnOn1:
            r.turnOn1 = pick(excluding: [roll.turnOn1, roll.turnOn2, roll.turnOff])
        case .turnOn2:
            r.turnOn2 = pick(excluding: [roll.turnOn1, roll.turnOn2, roll.turnOff])
        case .turnOff:
            r.turnOff = pick(excluding: [roll.turnOn1, roll.turnOn2, roll.turnOff])
        }
        return r
    }

    enum Slot { case aspiration, turnOn1, turnOn2, turnOff }

    private static func pick(excluding used: [Trait]) -> Trait {
        traits.filter { !used.contains($0) }.randomElement() ?? traits.randomElement()!
    }
}

// MARK: - View

struct AttractionRollerView: View {
    @State private var roll = Attraction.roll()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            slotRow("Aspiration", value: roll.aspiration, tint: .indigo) {
                roll = Attraction.reroll(roll, slot: .aspiration)
            }
            Divider()
            slotRow("Turn-On 1", value: roll.turnOn1.name, tint: .pink) {
                roll = Attraction.reroll(roll, slot: .turnOn1)
            }
            slotRow("Turn-On 2", value: roll.turnOn2.name, tint: .pink) {
                roll = Attraction.reroll(roll, slot: .turnOn2)
            }
            slotRow("Turn-Off", value: roll.turnOff.name, tint: .orange) {
                roll = Attraction.reroll(roll, slot: .turnOff)
            }

            HStack {
                Button {
                    roll = Attraction.roll()
                } label: {
                    Label("Roll All", systemImage: "dice")
                }
                .keyboardShortcut(.defaultAction)
                Button {
                    let text = """
                        Aspiration: \(roll.aspiration)
                        Turn-On 1: \(roll.turnOn1.name)
                        Turn-On 2: \(roll.turnOn2.name)
                        Turn-Off: \(roll.turnOff.name)
                        """
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .help("Copy the roll to the clipboard")
                Spacer()
            }
            .controlSize(.small)

            Text("\(Attraction.traits.count) traits — Nightlife + Bon Voyage")
                .font(.caption2).foregroundStyle(.tertiary)
        }
    }

    /// One slot: label, rolled value, and a dice to re-roll just that slot so
    /// you can keep the parts you like.
    private func slotRow(_ label: String, value: String, tint: Color,
                         reroll: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Text(label.uppercased())
                .font(.caption2).fontWeight(.semibold)
                .foregroundStyle(.secondary)
                .frame(width: 72, alignment: .leading)
            Text(value)
                .font(.title3).fontWeight(.medium)
                .foregroundStyle(tint)
                .textSelection(.enabled)
                .lineLimit(1).minimumScaleFactor(0.7)
            Spacer(minLength: 4)
            Button(action: reroll) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Re-roll just this one")
        }
    }
}
