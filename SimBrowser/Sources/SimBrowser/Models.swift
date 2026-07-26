import Foundation

struct Database: Decodable {
    var hoods: [Hood]
}

struct Hood: Decodable, Identifiable, Hashable {
    static func == (lhs: Hood, rhs: Hood) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }

    var id: String
    var name: String
    var sims: [Sim]
    var families: [Family]
}

struct Family: Decodable, Identifiable {
    var id: Int
    var lot: Int
    var funds: Int
    var name: String?
    var address: String?
    var memberNids: [Int]?
}

struct Relationship: Decodable, Hashable {
    var other: Int
    var daily: Int
    var lifetime: Int
    var flags: [String]
    var familyRel: String
    var bff: Bool
    var name: String
}

struct Sim: Decodable, Identifiable, Hashable {
    static func == (lhs: Sim, rhs: Sim) -> Bool { lhs.nid == rhs.nid && lhs.guid == rhs.guid }
    func hash(into hasher: inout Hasher) { hasher.combine(nid); hasher.combine(guid) }

    var nid: Int
    var guid: Int
    var familyId: Int
    var age: String
    var gender: String
    var zodiac: String
    var aspirations: [String]
    var aspirationScore: Int
    var career: String
    var careerTitle: String
    var careerLevel: Int
    var jobPerformance: Int
    var retiredCareer: String
    var retiredTitle: String
    var retiredLevel: Int
    var major: String
    var semester: Int
    var onCampus: Bool
    var grade: Int
    var prefMale: Int
    var prefFemale: Int
    var ghostFlags: Int
    var npcType: Int
    var fatness: Int
    var bodyFlags: Int
    var daysLeft: Int
    var personality: [String: Int]
    var skills: [String: Int]
    var interests: [String: Int]
    var first: String
    var last: String
    var bio: String
    var charFile: String
    var household: String
    var address: String
    var funds: Int
    var father: String
    var mother: String
    var spouse: String
    var siblings: [String]
    var children: [String]
    // Tie ids. Optional so a sims.json cached by an older build still decodes;
    // the family tree needs ids because full names are not unique within a hood.
    var fatherNid: Int?
    var motherNid: Int?
    var spouseNid: Int?
    var siblingNids: [Int]?
    var childrenNids: [Int]?
    var relationships: [Relationship]
    var orientation: String
    var loves: [String]
    var bestFriends: [String]
    var enemies: [String]

    var id: Int { nid }
    var fullName: String {
        let n = "\(first) \(last)".trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? "Sim #\(nid)" : n
    }
    /// Family instances at or above 0x7FDF are engine pools (townies, NPCs, adoption…)
    var isPlayable: Bool { familyId > 0 && familyId < 0x7FDF }
    var isNPC: Bool { npcType != 0 }
    /// In a real family but not placed on any lot (e.g. unplaced premade
    /// university students, families moved to the bin).
    var isInFamilyBin: Bool { isPlayable && !household.isEmpty && address.isEmpty }
    /// Nonzero ghost flags = deceased (verified against an in-game death:
    /// the record persists but ghost flags flip on and the family drops them).
    var isDead: Bool { ghostFlags != 0 }

    var careerDisplay: String {
        if !careerTitle.isEmpty { return careerTitle }
        if !career.isEmpty { return "\(career) (level \(careerLevel))" }
        return ""
    }
    var zodiacSymbol: String {
        [
            "Aries": "♈︎", "Taurus": "♉︎", "Gemini": "♊︎", "Cancer": "♋︎",
            "Leo": "♌︎", "Virgo": "♍︎", "Libra": "♎︎", "Scorpio": "♏︎",
            "Sagittarius": "♐︎", "Capricorn": "♑︎", "Aquarius": "♒︎", "Pisces": "♓︎",
        ][zodiac] ?? ""
    }
}
