import Foundation

/// Diffs two extracts of the save data and produces journal-ready lines,
/// e.g. "Aqua Smith married Captain Grunt", "Jonquil Delarosa went to college".
enum ChangeDetector {

    /// hood id → human-readable change lines
    static func diff(old: [Hood], new: [Hood]) -> [String: [String]] {
        var result: [String: [String]] = [:]
        let oldByID = Dictionary(uniqueKeysWithValues: old.map { ($0.id, $0) })
        for hood in new {
            guard let oldHood = oldByID[hood.id] else { continue }
            let lines = diffHood(old: oldHood, new: hood)
            if !lines.isEmpty { result[hood.id] = lines }
        }
        return result
    }

    private static func diffHood(old: Hood, new: Hood) -> [String] {
        var lines: [String] = []
        var oldByGUID: [Int: Sim] = [:]
        for s in old.sims { oldByGUID[s.guid] = s }
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
                        lines.append(line)
                    } else {
                        lines.append("\(sim.fullName) joined the neighborhood")
                    }
                }
                continue
            }
            guard sim.isPlayable || prev.isPlayable else { continue }
            lines.append(contentsOf: diffSim(prev: prev, cur: sim))
        }

        for prev in old.sims where prev.isPlayable && !prev.first.isEmpty && !seen.contains(prev.guid) {
            lines.append("\(prev.fullName) is no longer in the neighborhood (moved away)")
        }
        return lines
    }

    private static func diffSim(prev: Sim, cur: Sim) -> [String] {
        var lines: [String] = []
        let name = cur.fullName

        // Death: the record persists as a ghost; the family drops the sim.
        // Report it alone — the household/career fallout is just death's echo.
        if !prev.isDead && cur.isDead {
            return ["\(name) died"]
        }

        // Life stage
        if prev.age != cur.age {
            if cur.age == "Young Adult" {
                lines.append("\(name) went off to college")
            } else if prev.age == "Young Adult" && cur.age == "Adult" {
                lines.append("\(name) finished college and became an adult")
            } else {
                lines.append("\(name) aged from \(prev.age) to \(cur.age)")
            }
        }

        // Marriage
        if prev.spouse != cur.spouse {
            if prev.spouse.isEmpty {
                lines.append("\(name) married \(cur.spouse)")
            } else if cur.spouse.isEmpty {
                lines.append("\(name) is no longer married to \(prev.spouse)")
            } else {
                lines.append("\(name) is now married to \(cur.spouse) (was \(prev.spouse))")
            }
        }

        // Career
        if prev.career != cur.career || prev.careerLevel != cur.careerLevel {
            let title = cur.careerTitle.isEmpty ? cur.career : cur.careerTitle
            if prev.career.isEmpty && !cur.career.isEmpty {
                lines.append("\(name) got a job as \(title)")
            } else if cur.career.isEmpty && !prev.career.isEmpty {
                if !cur.retiredCareer.isEmpty && prev.retiredCareer.isEmpty {
                    let rt = cur.retiredTitle.isEmpty ? cur.retiredCareer : cur.retiredTitle
                    lines.append("\(name) retired as \(rt)")
                } else {
                    let pt = prev.careerTitle.isEmpty ? prev.career : prev.careerTitle
                    lines.append("\(name) left the \(pt) job")
                }
            } else if prev.career == cur.career {
                if cur.careerLevel > prev.careerLevel {
                    lines.append("\(name) was promoted to \(title)")
                } else if cur.careerLevel < prev.careerLevel {
                    lines.append("\(name) was demoted to \(title)")
                }
            } else if !cur.career.isEmpty {
                lines.append("\(name) changed careers and is now \(title)")
            }
        }

        // Household / address
        if prev.household != cur.household && !cur.household.isEmpty {
            var line = "\(name) moved in with the \(cur.household) family"
            if !cur.address.isEmpty { line += " at \(cur.address)" }
            lines.append(line)
        } else if prev.address != cur.address && !cur.address.isEmpty && !prev.address.isEmpty {
            lines.append("\(name)'s family moved to \(cur.address)")
        } else if prev.address.isEmpty && !cur.address.isEmpty && prev.isInFamilyBin {
            lines.append("\(name) moved out of the family bin to \(cur.address)")
        }

        // University
        if prev.major != cur.major && !cur.major.isEmpty && cur.major != "Undeclared" {
            lines.append("\(name) declared a major: \(cur.major)")
        }

        // Romance & feuds (additions only)
        for love in cur.loves where !prev.loves.contains(love) {
            lines.append("\(name) is in love with \(love)")
        }
        for enemy in cur.enemies where !prev.enemies.contains(enemy) {
            lines.append("\(name) became enemies with \(enemy)")
        }

        return lines
    }
}
