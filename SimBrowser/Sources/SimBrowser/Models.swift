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
    /// Optional so a sims.json cached by a build that ignored businesses still decodes.
    var businesses: [Business]?
}

/// An owned lot. Rank and customer loyalty come from a household token the
/// game only writes for a business run away from home, and only once it has
/// been opened — so a home business, and a community lot bought but never
/// opened, are both reported with no rank rather than left out.
struct Business: Decodable, Identifiable, Hashable {
    var lot: Int
    var name: String
    var ownerNid: Int
    var owner: String
    var ownerFamilyId: Int?
    var ownerHousehold: String
    var homeBusiness: Bool
    var rank: Int?
    var customerLoyalty: Int?

    var id: Int { lot }
    /// A home business's name is the household's address — the game never
    /// asks you to name one.
    var displayName: String { name.isEmpty ? "Lot \(lot)" : name }
    static let maxRank = 10
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

/// Open for Business perks, as `s2luastate.sim_perks` writes them: unspent
/// points, plus the bought perks of each track in tier order. Only tracks with
/// something bought appear — an untouched track is simply absent.
struct PerkState: Decodable, Hashable {
    var points: Int = 0
    var perks: [String: [String]] = [:]

    /// The five tracks in the order the in-game picker lays out its columns.
    /// Named here rather than read from the save because the save only tells us
    /// about tracks a sim has actually spent in, and an empty track is exactly
    /// what the progress display is for. Each track has five tiers.
    static let trackOrder = ["Connections", "Perception", "Cash", "Wholesale", "Motivation"]
    static let tiersPerTrack = 5

    var totalBought: Int { perks.values.reduce(0) { $0 + $1.count } }
    var isEmpty: Bool { points == 0 && totalBought == 0 }

    /// Every known track, bought perks first-tier-first, empty tracks included.
    var tracks: [(name: String, bought: [String])] {
        Self.trackOrder.map { ($0, perks[$0] ?? []) }
    }

    /// Anything the parser could not place in a known track — "Other" in
    /// practice, which a stock game never produces but a mod might.
    var unknownTracks: [(name: String, bought: [String])] {
        perks.filter { !Self.trackOrder.contains($0.key) }
            .sorted { $0.key < $1.key }
            .map { ($0.key, $0.value) }
    }
}

/// One talent badge, as `s2ngbh.sim_badges` writes it. Points run 0–1000 for
/// the three levels but are not capped there — the game keeps counting past
/// Gold, so a sim can sit at 1333 Sales.
struct Badge: Decodable, Hashable {
    var points: Int
    /// "Bronze", "Silver", "Gold", or empty below the first threshold at 333.
    var level: String

    /// The order the game's badge panel lists them, Open for Business first
    /// then the two Seasons additions. Badges are only recorded once a sim has
    /// some progress, so this is a display order, not a checklist.
    static let displayOrder = [
        "Sales", "Stocking", "Cash Register", "Robotics", "Toy Making",
        "Flower Arranging", "Cosmetology", "Gardening", "Fishing",
    ]
    static let thresholds = [("Bronze", 333), ("Silver", 666), ("Gold", 1000)]

    /// Position in `displayOrder`, for breaking ties on points.
    static func order(of name: String) -> Int {
        displayOrder.firstIndex(of: name) ?? displayOrder.count
    }

    /// How many of the three levels are earned, for the progress pips.
    var levelsEarned: Int { Badge.thresholds.filter { points >= $0.1 }.count }
}

/// A sim's lifetime want, as `s2ltw.evaluate` writes it.
///
/// The game never stores how far along a want is — the aspiration panel
/// recomputes it from a check-tree BHAV every time it draws — so `progress` is
/// reconstructed from the save and is nil for wants nothing has decoded yet.
/// `confidence` says how much to trust it: "exact" reimplements what the check
/// tree counts, "approx" is a defensible stand-in, "unknown" means no evaluator.
struct LifetimeWant: Decodable, Hashable {
    var name: String
    var target: Int
    var progress: Int?
    var done: Bool
    /// What the number was counted from, e.g. "level in Adult - Culinary".
    var basis: String
    var confidence: String
    /// The things behind the count — partner names, maxed skills — when the
    /// evaluator can name them. Empty for the ones that only yield a number.
    var detail: [String]

    var isMeasured: Bool { progress != nil }
    var isApproximate: Bool { confidence == "approx" }

    /// 0–1 for a bar, clamped: a sim can overshoot a want they already met.
    var fraction: Double {
        guard let progress, target > 0 else { return done ? 1 : 0 }
        return min(1, max(0, Double(progress) / Double(target)))
    }

    /// "8 / 20", or "? / 20" where progress could not be reconstructed.
    var tally: String { "\(progress.map(String.init) ?? "?") / \(target)" }
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
    /// Optional so a sims.json cached by a build that ignored these still decodes.
    var perks: PerkState?
    var badges: [String: Badge]?
    /// Nil for children and toddlers, who have not been given a want yet.
    var ltw: LifetimeWant?
    /// Names of the businesses the sim's *household* owns — the parser gives
    /// every member the same list. Which of them this sim personally owns
    /// comes from `Hood.businesses`, which is also the only place the rank is.
    var businesses: [String]?

    var id: Int { nid }
    var perkState: PerkState { perks ?? PerkState() }
    var hasBusinessPerks: Bool { !perkState.isEmpty }

    /// Earned badges, strongest first — a sim usually has one or two, and
    /// which they are matters more than the game's fixed panel order.
    var rankedBadges: [(name: String, badge: Badge)] {
        (badges ?? [:])
            .map { (name: $0.key, badge: $0.value) }
            .sorted {
                $0.badge.points != $1.badge.points
                    ? $0.badge.points > $1.badge.points
                    : Badge.order(of: $0.name) < Badge.order(of: $1.name)
            }
    }
    var hasBadges: Bool { !(badges ?? [:]).isEmpty }
    var ownsBusiness: Bool { !(businesses ?? []).isEmpty }
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
