import Foundation

/// What kind of thing happened. Drives grouping order and the icon in the
/// review sheet.
enum ChangeCategory: String, Codable, CaseIterable {
    case death, birth, lifeStage, romance, family, household
    case career, education, skills, social, finances, misc

    /// Big life events sort above the small stuff inside a household.
    var rank: Int {
        switch self {
        case .death: return 0
        case .birth: return 1
        case .lifeStage: return 2
        case .romance: return 3
        case .family: return 4
        case .household: return 5
        case .career: return 6
        case .education: return 7
        case .social: return 8
        case .skills: return 9
        case .misc: return 10
        case .finances: return 11
        }
    }

    var symbol: String {
        switch self {
        case .death: return "moon.stars"
        case .birth: return "stroller"
        case .lifeStage: return "birthday.cake"
        case .romance: return "heart"
        case .family: return "figure.2.and.child.holdinghands"
        case .household: return "house"
        case .career: return "briefcase"
        case .education: return "graduationcap"
        case .skills: return "chart.bar"
        case .social: return "person.2"
        case .finances: return "banknote"
        case .misc: return "sparkle"
        }
    }
}

/// One journal-ready line, e.g. "Aqua Smith married Captain Grunt", tagged with
/// the household it belongs to so the digest can be grouped for rotational play.
struct Change: Codable, Hashable, Identifiable {
    var id = UUID()
    var household: String
    var text: String
    var category: ChangeCategory
    /// Set on events that both sims report (marriages, engagements, feuds) so
    /// the reciprocal copy can be dropped.
    var dedupe: String?

    init(_ text: String, household: String, category: ChangeCategory, dedupe: String? = nil) {
        self.household = household
        self.text = text
        self.category = category
        self.dedupe = dedupe
    }
}

/// Diffs two extracts of the save data and produces journal-ready changes,
/// e.g. "Aqua Smith married Captain Grunt", "Jonquil Delarosa went to college".
enum ChangeDetector {

    /// Skill/personality values are stored as points × 100.
    private static let pointScale = 100
    private static let skillOrder = ["Cooking", "Mechanical", "Charisma", "Body",
                                     "Logic", "Creativity", "Cleaning"]
    private static let personalityOrder = ["Neat", "Outgoing", "Active", "Playful", "Nice"]
    /// Family instances at or above this are engine pools, not real households.
    private static let firstPoolFamilyID = 0x7FDF

    /// hood id → changes detected in that hood
    static func diff(old: [Hood], new: [Hood]) -> [String: [Change]] {
        var result: [String: [Change]] = [:]
        let oldByID = Dictionary(uniqueKeysWithValues: old.map { ($0.id, $0) })
        for hood in new {
            guard let oldHood = oldByID[hood.id] else { continue }
            let changes = diffHood(old: oldHood, new: hood)
            if !changes.isEmpty { result[hood.id] = changes }
        }
        return result
    }

    // MARK: - Hood

    private static func diffHood(old: Hood, new: Hood) -> [Change] {
        var changes: [Change] = []

        // Households first: a whole family relocating is one event, not one per
        // member, so the sim pass needs to know which moves are already covered.
        let (householdChanges, relocated) = diffFamilies(old: old, new: new)
        changes += householdChanges

        var oldByGUID: [Int: Sim] = [:]
        for s in old.sims { oldByGUID[s.guid] = s }
        let oldNids = Set(old.sims.map { $0.nid })
        var seen = Set<Int>()

        for sim in new.sims {
            seen.insert(sim.guid)
            guard !sim.first.isEmpty else { continue }  // nameless placeholder records
            guard let prev = oldByGUID[sim.guid] else {
                // Newly appeared — only report real family members, not townie churn
                if sim.isPlayable {
                    if sim.age == "Baby" {
                        var line = "\(sim.fullName) was born"
                        let parents = [sim.mother, sim.father].filter { !$0.isEmpty }
                        if !parents.isEmpty { line += " to \(parents.joined(separator: " and "))" }
                        changes.append(Change(line, household: sim.household, category: .birth))
                    } else {
                        changes.append(Change("\(sim.fullName) joined the neighborhood",
                                              household: sim.household, category: .household))
                    }
                }
                continue
            }
            guard sim.isPlayable || prev.isPlayable else { continue }
            changes += diffSim(prev: prev, cur: sim, relocated: relocated, knownNids: oldNids)
        }

        for prev in old.sims where prev.isPlayable && !prev.first.isEmpty && !seen.contains(prev.guid) {
            changes.append(Change("\(prev.fullName) is no longer in the neighborhood (moved away)",
                                  household: prev.household, category: .household))
        }

        return dedupe(changes)
    }

    /// Drops the second copy of events both participants report.
    private static func dedupe(_ changes: [Change]) -> [Change] {
        var seenKeys = Set<String>()
        return changes.filter { change in
            guard let key = change.dedupe else { return true }
            return seenKeys.insert(key).inserted
        }
    }

    // MARK: - Households

    /// Household-level events, plus the ids of households that moved lot
    /// (whose members' address changes are therefore already accounted for).
    private static func diffFamilies(old: Hood, new: Hood) -> ([Change], Set<Int>) {
        var changes: [Change] = []
        var relocated = Set<Int>()
        let oldByID = Dictionary(old.families.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var seen = Set<Int>()

        for fam in new.families where fam.id > 0 && fam.id < firstPoolFamilyID {
            seen.insert(fam.id)
            let name = fam.name ?? "Family \(fam.id)"
            let them = ChangeDigest.householdPhrase(name)
            let address = fam.address ?? ""
            guard let prev = oldByID[fam.id] else {
                var line = "\(them) was created"
                if !address.isEmpty { line += " at \(address)" }
                changes.append(Change(line, household: name, category: .household))
                continue
            }

            if prev.lot != fam.lot {
                relocated.insert(fam.id)
                if fam.lot == 0 {
                    changes.append(Change("\(them) moved into the family bin",
                                          household: name, category: .household))
                } else if address.isEmpty {
                    changes.append(Change("\(them) moved to a new lot",
                                          household: name, category: .household))
                } else {
                    changes.append(Change("\(them) moved to \(address)",
                                          household: name, category: .household))
                }
            }

            // Only worth a line when the balance really shifted — every played
            // season nudges funds by a few simoleons of bills and groceries.
            let delta = fam.funds - prev.funds
            if abs(delta) >= 1_000 {
                let verb = delta > 0 ? "earned" : "spent"
                changes.append(Change(
                    "\(them) \(verb) \(money(abs(delta))) (now \(money(fam.funds)))",
                    household: name, category: .finances))
            }
        }

        for prev in old.families
        where prev.id > 0 && prev.id < firstPoolFamilyID && !seen.contains(prev.id) {
            let name = prev.name ?? "Family \(prev.id)"
            changes.append(Change("\(ChangeDigest.householdPhrase(name)) no longer exists",
                                  household: name, category: .household))
        }
        return (changes, relocated)
    }

    // MARK: - Sims

    private static func diffSim(prev: Sim, cur: Sim,
                                relocated: Set<Int>, knownNids: Set<Int>) -> [Change] {
        var changes: [Change] = []
        let name = cur.fullName
        // Dead sims keep their record but leave their family, so fall back to
        // where they lived when they were alive.
        let house = cur.household.isEmpty ? prev.household : cur.household

        func add(_ text: String, _ category: ChangeCategory, dedupe: String? = nil) {
            changes.append(Change(text, household: house, category: category, dedupe: dedupe))
        }

        // Death: the record persists as a ghost; the family drops the sim.
        // Report it alone — the household/career fallout is just death's echo.
        if !prev.isDead && cur.isDead {
            return [Change("\(name) died", household: house, category: .death)]
        }
        // Ghosts still accrue relationship drift. Nothing they "do" is news.
        if prev.isDead && cur.isDead { return [] }

        // Life stage
        var collegeReported = false
        if prev.age != cur.age {
            if cur.age == "Young Adult" {
                add("\(name) went off to college", .education)
                collegeReported = true
            } else if prev.age == "Young Adult" && cur.age == "Adult" {
                add("\(name) finished college and became an adult", .education)
                collegeReported = true
            } else {
                add("\(name) aged from \(prev.age) to \(cur.age)", .lifeStage)
            }
        }

        // Marriage
        if prev.spouse != cur.spouse {
            if prev.spouse.isEmpty {
                add("\(name) married \(cur.spouse)", .romance, dedupe: pairKey("married", name, cur.spouse))
            } else if cur.spouse.isEmpty {
                add("\(name) is no longer married to \(prev.spouse)", .romance,
                    dedupe: pairKey("divorced", name, prev.spouse))
            } else {
                add("\(name) is now married to \(cur.spouse) (was \(prev.spouse))", .romance,
                    dedupe: pairKey("married", name, cur.spouse))
            }
        }

        // Career
        if prev.career != cur.career || prev.careerLevel != cur.careerLevel {
            let title = cur.careerTitle.isEmpty ? cur.career : cur.careerTitle
            if prev.career.isEmpty && !cur.career.isEmpty {
                add("\(name) got a job as \(title)", .career)
            } else if cur.career.isEmpty && !prev.career.isEmpty {
                if !cur.retiredCareer.isEmpty && prev.retiredCareer.isEmpty {
                    let rt = cur.retiredTitle.isEmpty ? cur.retiredCareer : cur.retiredTitle
                    add("\(name) retired as \(rt)", .career)
                } else {
                    let pt = prev.careerTitle.isEmpty ? prev.career : prev.careerTitle
                    add("\(name) left the \(pt) job", .career)
                }
            } else if prev.career == cur.career {
                if cur.careerLevel > prev.careerLevel {
                    add("\(name) was promoted to \(title)", .career)
                } else if cur.careerLevel < prev.careerLevel {
                    add("\(name) was demoted to \(title)", .career)
                }
            } else if !cur.career.isEmpty {
                add("\(name) changed careers and is now \(title)", .career)
            }
        }

        // Household / address. Keyed on family id, not household name: the name
        // is just the members' commonest surname, so it flips on its own when
        // somebody moves out — which would read as everyone left behind moving.
        if prev.familyId != cur.familyId && !cur.household.isEmpty {
            var line = "\(name) moved in with the \(cur.household) household"
            if !cur.address.isEmpty { line += " at \(cur.address)" }
            add(line, .household)
        } else if prev.address != cur.address && !cur.address.isEmpty && !prev.address.isEmpty {
            // Suppressed when the whole household relocated — that is one event,
            // already reported against the family.
            if !relocated.contains(cur.familyId) {
                add("\(name)'s family moved to \(cur.address)", .household)
            }
        } else if prev.address.isEmpty && !cur.address.isEmpty && prev.isInFamilyBin {
            if !relocated.contains(cur.familyId) {
                add("\(name) moved out of the family bin to \(cur.address)", .household)
            }
        }

        // University
        if prev.major != cur.major && !cur.major.isEmpty && cur.major != "Undeclared" {
            add("\(name) declared a major: \(cur.major)", .education)
        }
        if cur.onCampus && cur.semester > prev.semester && cur.semester > 0 {
            add("\(name) started semester \(cur.semester) of university", .education)
        }
        if prev.onCampus && !cur.onCampus && !collegeReported {
            add("\(name) came home from university", .education)
        }

        // Aspiration. "Grow Up" is the placeholder children carry, so losing it
        // is just the teen birthday, not a change of heart.
        for asp in cur.aspirations where !prev.aspirations.contains(asp) && asp != "Grow Up" {
            if prev.aspirations.filter({ $0 != "Grow Up" }).isEmpty {
                add("\(name) took up the \(asp) aspiration", .lifeStage)
            } else {
                add("\(name) added a secondary aspiration: \(asp)", .lifeStage)
            }
        }

        // A divorce clears the love flag too; the marriage line already says it.
        let exSpouse = (cur.spouse != prev.spouse && !prev.spouse.isEmpty) ? prev.spouse : nil
        changes += relationshipChanges(prev: prev, cur: cur, house: house, exSpouse: exSpouse)
        changes += skillChanges(prev: prev, cur: cur, house: house)

        // Personality only shifts through rewards, elixirs and similar — rare
        // enough that any whole-point move is worth a line.
        let shifts = personalityOrder.compactMap { trait -> String? in
            let d = points(cur.personality[trait] ?? 0) - points(prev.personality[trait] ?? 0)
            return d == 0 ? nil : "\(trait) \(d > 0 ? "+" : "")\(d)"
        }
        if !shifts.isEmpty {
            add("\(name)'s personality shifted: \(shifts.joined(separator: ", "))", .misc)
        }

        // Body shape, at a threshold that only real gym or fridge time crosses.
        let fatDelta = cur.fatness - prev.fatness
        if fatDelta <= -250 {
            add("\(name) has slimmed down", .misc)
        } else if fatDelta >= 250 {
            add("\(name) has put on weight", .misc)
        }

        // New parent ties to sims that already existed — i.e. adoption rather
        // than birth, which arrives as a brand new sim record instead.
        // `children` and `childrenNids` are the same tie list, so they line up.
        for (idx, childNid) in (cur.childrenNids ?? []).enumerated()
        where !(prev.childrenNids ?? []).contains(childNid) {
            guard knownNids.contains(childNid),
                  let childName = cur.children[safe: idx], !childName.isEmpty else { continue }
            add("\(name) became a parent to \(childName)", .family,
                dedupe: pairKey("parent-\(childNid)", name, childName))
        }

        return changes
    }

    // MARK: - Relationships

    /// Flag deltas on the pairwise relationship records: engagements, crushes,
    /// break-ups, best friends and feuds. Only records present on both sides of
    /// the diff are compared, so a sim leaving the hood doesn't read as a
    /// break-up.
    private static func relationshipChanges(prev: Sim, cur: Sim, house: String,
                                            exSpouse: String?) -> [Change] {
        var changes: [Change] = []
        let name = cur.fullName
        let prevByNid = Dictionary(prev.relationships.map { ($0.other, $0) },
                                  uniquingKeysWith: { a, _ in a })

        func add(_ text: String, _ category: ChangeCategory, _ kind: String, _ other: String) {
            changes.append(Change(text, household: house, category: category,
                                  dedupe: pairKey(kind, name, other)))
        }

        for rel in cur.relationships {
            let other = rel.name
            let now = Set(rel.flags)
            guard let was = prevByNid[rel.other].map({ Set($0.flags) }) else {
                // Brand new record: report only the standout states, or every
                // new acquaintance would file a report. A record can also be
                // new because a weak friendship fell below the extractor's
                // keep threshold and came back stronger.
                if now.contains("Engaged") {
                    add("\(name) got engaged to \(other)", .romance, "engaged", other)
                } else if now.contains("Love") {
                    add("\(name) fell in love with \(other)", .romance, "love", other)
                } else if now.contains("Enemy") {
                    add("\(name) became enemies with \(other)", .social, "enemy", other)
                } else if now.contains("Best Friend") || rel.bff {
                    add("\(name) and \(other) became best friends", .social, "bff", other)
                }
                continue
            }
            let wasBFF = was.contains("Best Friend") || (prevByNid[rel.other]?.bff ?? false)
            let isBFF = now.contains("Best Friend") || rel.bff

            if now.contains("Engaged") && !was.contains("Engaged") {
                add("\(name) got engaged to \(other)", .romance, "engaged", other)
            }
            if was.contains("Engaged") && !now.contains("Engaged") && !now.contains("Married") {
                add("\(name) and \(other) called off their engagement", .romance, "unengaged", other)
            }
            if now.contains("Steady") && !was.contains("Steady") {
                add("\(name) and \(other) started going steady", .romance, "steady", other)
            }
            if now.contains("Love") && !was.contains("Love") {
                add("\(name) fell in love with \(other)", .romance, "love", other)
            }
            if was.contains("Love") && !now.contains("Love") && other != exSpouse {
                add("\(name) fell out of love with \(other)", .romance, "unlove", other)
            }
            if now.contains("Crush") && !was.contains("Crush") && !now.contains("Love") {
                add("\(name) has a crush on \(other)", .romance, "crush", other)
            }
            if isBFF && !wasBFF {
                add("\(name) and \(other) became best friends", .social, "bff", other)
            }
            if wasBFF && !isBFF {
                add("\(name) and \(other) are no longer best friends", .social, "unbff", other)
            }
            if now.contains("Enemy") && !was.contains("Enemy") {
                add("\(name) became enemies with \(other)", .social, "enemy", other)
            }
            if was.contains("Enemy") && !now.contains("Enemy") {
                add("\(name) and \(other) patched things up", .social, "unenemy", other)
            }
        }
        return changes
    }

    // MARK: - Skills

    private static func skillChanges(prev: Sim, cur: Sim, house: String) -> [Change] {
        var gains: [String] = []
        var maxed: [String] = []
        for skill in skillOrder {
            let before = prev.skills[skill] ?? 0
            let after = cur.skills[skill] ?? 0
            let delta = points(after) - points(before)
            guard delta > 0 else { continue }
            if points(after) >= 10 && points(before) < 10 {
                maxed.append(skill)
            } else {
                gains.append("\(skill) +\(delta)")
            }
        }
        var changes: [Change] = []
        if !maxed.isEmpty {
            changes.append(Change("\(cur.fullName) maxed out \(list(maxed))",
                                  household: house, category: .skills))
        }
        if !gains.isEmpty {
            changes.append(Change("\(cur.fullName) gained skill points: \(gains.joined(separator: ", "))",
                                  household: house, category: .skills))
        }
        return changes
    }

    // MARK: - Helpers

    private static func points(_ raw: Int) -> Int { raw / pointScale }

    private static func money(_ v: Int) -> String { "§\(v.formatted())" }

    /// Order-independent key so "A married B" and "B married A" collapse.
    private static func pairKey(_ kind: String, _ a: String, _ b: String) -> String {
        "\(kind)|\([a, b].sorted().joined(separator: "|"))"
    }

    private static func list(_ items: [String]) -> String {
        guard items.count > 1 else { return items.first ?? "" }
        return items.dropLast().joined(separator: ", ") + " and " + items[items.count - 1]
    }
}

// MARK: - Digest

/// Turns a pile of changes into the per-household shape a rotational player
/// wants: one section per family, biggest events first.
enum ChangeDigest {
    struct Group: Identifiable {
        var id: String { household }
        var household: String
        var changes: [Change]
        /// Section heading — households read as "The Goth household".
        var title: String {
            household.isEmpty ? "Around the neighborhood" : householdPhrase(household)
        }
    }

    /// "Goth" → "The Goth household". Households with no shared surname are
    /// already named "Family 16" by the extractor, which takes no article.
    static func householdPhrase(_ name: String) -> String {
        name.hasPrefix("Family ") ? name : "The \(name) household"
    }

    static func grouped(_ changes: [Change]) -> [Group] {
        let byHousehold = Dictionary(grouping: changes) { $0.household }
        return byHousehold
            .map { household, items in
                Group(household: household,
                      changes: items.sorted { $0.category.rank < $1.category.rank })
            }
            // Unhoused odds and ends last; everything else alphabetical.
            .sorted { a, b in
                if a.household.isEmpty != b.household.isEmpty { return b.household.isEmpty }
                return a.household.localizedStandardCompare(b.household) == .orderedAscending
            }
    }

    /// Markdown-ish text appended to a journal entry.
    static func journalText(_ changes: [Change]) -> String {
        grouped(changes).map { group in
            "\(group.title)\n" + group.changes.map { "- \($0.text)" }.joined(separator: "\n")
        }
        .joined(separator: "\n\n")
    }
}
