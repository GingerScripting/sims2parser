import SwiftUI

struct SimDetailView: View {
    let sim: Sim
    let hood: Hood?
    var onSelect: (Sim) -> Void
    var journalMentions: [JournalMention] = []
    var onOpenJournal: (UUID) -> Void = { _ in }
    var onShowFamilyTree: () -> Void = {}

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                infoGrid
                if !sim.bio.isEmpty { bioSection }
                familySection
                if !journalMentions.isEmpty { journalSection }
                traitsSection
                if sim.hasBadges { badgesSection }
                if !householdBusinesses.isEmpty { businessesSection }
                if sim.hasBusinessPerks { businessPerksSection }
                relationshipsSection
                footer
            }
            .padding(32)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .center)
    }

    // MARK: header

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(sim.fullName).font(.system(size: 30, weight: .bold))
                if !sim.zodiac.isEmpty {
                    Text("\(sim.zodiacSymbol) \(sim.zodiac)")
                        .font(.title3).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 6) {
                if sim.isDead { badge("Deceased", color: .gray) }
                badge(sim.age, color: .blue)
                badge(sim.gender, color: sim.gender == "Female" ? .pink : .teal)
                ForEach(sim.aspirations, id: \.self) { badge($0, color: .indigo) }
                if !sim.orientation.isEmpty { badge(sim.orientation, color: .mint) }
                if sim.isNPC { badge("NPC", color: .gray) }
                if !sim.isPlayable && !sim.isNPC { badge("Townie", color: .gray) }
                if sim.onCampus { badge("At university", color: .green) }
                if sim.isInFamilyBin { badge("Family Bin", color: .brown) }
            }
        }
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption).fontWeight(.medium)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    // MARK: info

    private var infoGrid: some View {
        section("Overview") {
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                if !sim.household.isEmpty {
                    row("Household", "\(sim.household) family")
                    row("Address", sim.isInFamilyBin ? "Family Bin (not placed on a lot)"
                                                     : (sim.address.isEmpty ? "—" : sim.address))
                    row("Funds", "§\(sim.funds.formatted())")
                }
                if !sim.careerDisplay.isEmpty {
                    row("Career", careerLine)
                }
                if !sim.retiredCareer.isEmpty {
                    row("Retired from", retiredLine)
                }
                if !sim.major.isEmpty {
                    row("Major", sim.onCampus ? "\(sim.major) (semester \(sim.semester))" : sim.major)
                }
                if sim.age == "Child" || sim.age == "Teen" {
                    row("School grade", grade(sim.grade))
                }
            }
        }
    }

    private var careerLine: String {
        var s = sim.careerTitle.isEmpty ? sim.career : sim.careerTitle
        let track = sim.career
            .replacingOccurrences(of: "Adult - ", with: "")
            .replacingOccurrences(of: "Teen Elder - ", with: "")
        if !sim.careerTitle.isEmpty { s += " — \(track), level \(sim.careerLevel)" }
        return s
    }

    private var retiredLine: String {
        let track = sim.retiredCareer
            .replacingOccurrences(of: "Adult - ", with: "")
            .replacingOccurrences(of: "Teen Elder - ", with: "")
        return sim.retiredTitle.isEmpty ? track : "\(sim.retiredTitle) (\(track))"
    }

    private func grade(_ g: Int) -> String {
        let letters = ["F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
        return g < letters.count ? letters[g] : "\(g)"
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            Text(value).textSelection(.enabled)
        }
    }

    // MARK: bio

    private var bioSection: some View {
        section("Bio") {
            Text(sim.bio)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: family

    private var hasFamilyTies: Bool {
        !(sim.mother.isEmpty && sim.father.isEmpty && sim.spouse.isEmpty
          && sim.siblings.isEmpty && sim.children.isEmpty)
    }

    private var familySection: some View {
        section("Family") {
            VStack(alignment: .leading, spacing: 12) {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                    if !sim.mother.isEmpty { linkRow("Mother", [sim.mother]) }
                    if !sim.father.isEmpty { linkRow("Father", [sim.father]) }
                    if !sim.spouse.isEmpty { linkRow("Spouse", [sim.spouse]) }
                    if !sim.siblings.isEmpty { linkRow("Siblings", sim.siblings) }
                    if !sim.children.isEmpty { linkRow("Children", sim.children) }
                    if !hasFamilyTies {
                        GridRow { Text("No recorded family ties").foregroundStyle(.tertiary) }
                    }
                }
                if hasFamilyTies {
                    Button(action: onShowFamilyTree) {
                        Label("View family tree", systemImage: "person.2.crop.square.stack")
                    }
                    .controlSize(.small)
                }
            }
        }
    }

    private func linkRow(_ label: String, _ names: [String]) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            // Wrapping list of tappable names
            FlowLayout(spacing: 6) {
                ForEach(names, id: \.self) { name in
                    simLink(name)
                }
            }
        }
    }

    @ViewBuilder
    private func simLink(_ name: String) -> some View {
        if let target = hood?.sims.first(where: { $0.fullName == name }) {
            Button(name) { onSelect(target) }
                .buttonStyle(.link)
        } else {
            Text(name)
        }
    }

    // MARK: journal

    private var journalSection: some View {
        section("Journal") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(journalMentions) { mention in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Button(mention.title) { onOpenJournal(mention.id) }
                            .buttonStyle(.link)
                            .frame(minWidth: 76, alignment: .leading)
                        Text(mention.line)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }
            }
        }
    }

    // MARK: traits

    private let personalityOrder = ["Neat", "Outgoing", "Active", "Playful", "Nice"]
    private let skillOrder = ["Cooking", "Mechanical", "Charisma", "Body", "Logic", "Creativity", "Cleaning"]

    private var traitsSection: some View {
        section("Personality & Skills") {
            HStack(alignment: .top, spacing: 40) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Personality").font(.caption).foregroundStyle(.secondary)
                    ForEach(personalityOrder, id: \.self) { name in
                        barRow(name, sim.personality[name] ?? 0, tint: .indigo)
                    }
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text("Skills").font(.caption).foregroundStyle(.secondary)
                    ForEach(skillOrder, id: \.self) { name in
                        barRow(name, sim.skills[name] ?? 0, tint: .teal)
                    }
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text("Top interests").font(.caption).foregroundStyle(.secondary)
                    ForEach(topInterests, id: \.0) { name, v in
                        barRow(name, v, tint: .orange)
                    }
                }
            }
        }
    }

    private var topInterests: [(String, Int)] {
        sim.interests.sorted { $0.value > $1.value }.prefix(5).map { ($0.key, $0.value) }
    }

    private func barRow(_ name: String, _ value: Int, tint: Color) -> some View {
        HStack(spacing: 8) {
            Text(name).frame(width: 82, alignment: .leading).font(.callout)
            GeometryReader { _ in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule().fill(tint.opacity(0.8))
                        .frame(width: max(3, 90 * CGFloat(min(value, 1000)) / 1000))
                }
            }
            .frame(width: 90, height: 7)
            Text("\(value / 100)").font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                .frame(width: 18, alignment: .trailing)
        }
    }

    // MARK: talent badges

    private var badgesSection: some View {
        section("Talent Badges") {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(sim.rankedBadges, id: \.name) { name, badge in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(name).font(.callout).frame(width: 120, alignment: .leading)
                        HStack(spacing: 3) {
                            ForEach(0..<Badge.thresholds.count, id: \.self) { i in
                                Circle()
                                    .fill(i < badge.levelsEarned
                                          ? AnyShapeStyle(badgeColor(Badge.thresholds[i].0))
                                          : AnyShapeStyle(.quaternary))
                                    .frame(width: 7, height: 7)
                            }
                        }
                        .frame(width: 34, alignment: .leading)
                        // Below 333 the game has recorded progress but awarded
                        // nothing, which is worth showing as its own state.
                        Text(badge.level.isEmpty ? "In progress" : badge.level)
                            .font(.callout)
                            .foregroundStyle(badge.level.isEmpty
                                             ? AnyShapeStyle(.tertiary)
                                             : AnyShapeStyle(badgeColor(badge.level)))
                            .frame(width: 80, alignment: .leading)
                        Text("\(badge.points)")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
    }

    private func badgeColor(_ level: String) -> Color {
        switch level {
        case "Gold": return .yellow
        case "Silver": return .gray
        case "Bronze": return .brown
        default: return .secondary
        }
    }

    // MARK: businesses

    /// Everything the sim's household owns. The parser gives each member the
    /// same list of names, so ownership within the household is settled by the
    /// hood-level records, which are also the only place a rank lives.
    private var householdBusinesses: [Business] {
        guard let hood, let all = hood.businesses else {
            // No hood in hand (or data from an older build): fall back to the
            // names on the sim, which is all that is left to show.
            return (sim.businesses ?? []).map {
                Business(lot: 0, name: $0, ownerNid: 0, owner: "", ownerFamilyId: nil,
                         ownerHousehold: "", homeBusiness: false,
                         rank: nil, customerLoyalty: nil)
            }
        }
        return all.filter { $0.ownerFamilyId == sim.familyId }
            .sorted {
                ($0.rank ?? -1) != ($1.rank ?? -1)
                    ? ($0.rank ?? -1) > ($1.rank ?? -1)      // best-ranked first
                    : $0.displayName < $1.displayName
            }
    }

    private var businessesSection: some View {
        section("Businesses") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(householdBusinesses.enumerated()), id: \.offset) { _, biz in
                    businessRow(biz)
                }
            }
        }
    }

    private func businessRow(_ biz: Business) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(biz.displayName).font(.callout).fontWeight(.medium)
                if biz.ownerNid == 0 {
                    // The household holds a business token for this lot but no
                    // sim is recorded as its owner, so home vs community is not
                    // ours to assert.
                    chip("No recorded owner", .gray)
                } else {
                    chip(biz.homeBusiness ? "Home" : "Community", biz.homeBusiness ? .brown : .teal)
                }
                if biz.ownerNid != sim.nid && !biz.owner.isEmpty {
                    Text("owned by").font(.caption).foregroundStyle(.tertiary)
                    simLink(biz.owner).font(.caption)
                }
            }
            HStack(spacing: 10) {
                if let rank = biz.rank {
                    Text("Rank \(rank) of \(Business.maxRank)")
                    if let loyal = biz.customerLoyalty {
                        Text("· \(loyal) loyal customer\(loyal == 1 ? "" : "s")")
                    }
                } else if biz.homeBusiness {
                    // The rank token is only written for a business away from
                    // home, and only after its first opening.
                    Text("Rank kept in the lot, not the neighborhood")
                } else {
                    Text("No rank recorded — not opened yet")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    // MARK: business perks

    /// Only a handful of sims in a hood own any, so the whole section is
    /// omitted unless there is something to show — including the case of
    /// unspent points and nothing bought, which is the state worth noticing.
    private var businessPerksSection: some View {
        let state = sim.perkState
        return section("Business Perks") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Text("\(state.totalBought) of \(PerkState.trackOrder.count * PerkState.tiersPerTrack) bought")
                        .font(.callout).foregroundStyle(.secondary)
                    if state.points > 0 {
                        chip("\(state.points) unspent point\(state.points == 1 ? "" : "s")", .orange)
                    }
                }
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(state.tracks, id: \.name) { track in
                        perkTrackRow(track.name, bought: track.bought)
                    }
                    // Modded perks have no known tier count, so no progress pips.
                    ForEach(state.unknownTracks, id: \.name) { track in
                        perkTrackRow(track.name, bought: track.bought, showProgress: false)
                    }
                }
            }
        }
    }

    private func perkTrackRow(_ name: String, bought: [String], showProgress: Bool = true) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(name)
                .font(.callout)
                .foregroundStyle(bought.isEmpty ? AnyShapeStyle(.tertiary) : AnyShapeStyle(.primary))
                .frame(width: 92, alignment: .leading)
            HStack(spacing: 3) {
                if showProgress {
                    ForEach(0..<PerkState.tiersPerTrack, id: \.self) { tier in
                        Circle()
                            .fill(tier < bought.count ? AnyShapeStyle(Color.orange) : AnyShapeStyle(.quaternary))
                            .frame(width: 7, height: 7)
                    }
                }
            }
            .frame(width: 50, alignment: .leading)
            // Tier order, so the arrows read as the sequence they were bought in.
            Text(bought.isEmpty ? "—" : bought.joined(separator: " › "))
                .font(.callout)
                .foregroundStyle(bought.isEmpty ? .tertiary : .secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    // MARK: relationships

    private var relationshipsSection: some View {
        section("Relationships") {
            if sim.relationships.isEmpty {
                Text("No significant relationships").foregroundStyle(.tertiary)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(sim.relationships.prefix(40).enumerated()), id: \.offset) { _, rel in
                        relRow(rel)
                        Divider().opacity(0.4)
                    }
                }
            }
        }
    }

    private func relRow(_ rel: Relationship) -> some View {
        HStack(spacing: 10) {
            simLink(rel.name).frame(minWidth: 150, alignment: .leading)
            Text("\(rel.daily >= 0 ? "+" : "")\(rel.daily)")
                .font(.callout.monospacedDigit())
                .foregroundStyle(scoreColor(rel.daily))
                .frame(width: 42, alignment: .trailing)
            Text("LT \(rel.lifetime >= 0 ? "+" : "")\(rel.lifetime)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 56, alignment: .trailing)
            HStack(spacing: 4) {
                if !rel.familyRel.isEmpty { chip(rel.familyRel, .brown) }
                ForEach(rel.flags, id: \.self) { f in
                    chip(f, flagColor(f))
                }
                if rel.bff { chip("BFF", .cyan) }
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }

    private func chip(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2)
            .padding(.horizontal, 6).padding(.vertical, 1.5)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private func flagColor(_ flag: String) -> Color {
        switch flag {
        case "Love", "Crush": return .pink
        case "Married", "Engaged", "Steady": return .red
        case "Enemy": return .orange
        case "Best Friend": return .blue
        default: return .green
        }
    }

    private func scoreColor(_ v: Int) -> Color {
        if v >= 70 { return .blue }
        if v >= 20 { return .green }
        if v > -20 { return .secondary }
        return .red
    }

    // MARK: footer

    private var footer: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Sim #\(sim.nid) · GUID 0x\(String(format: "%08X", sim.guid))")
            if !sim.charFile.isEmpty { Text(sim.charFile) }
        }
        .font(.caption2)
        .foregroundStyle(.tertiary)
        .textSelection(.enabled)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption).fontWeight(.semibold)
                .foregroundStyle(.secondary)
                .kerning(0.5)
            content()
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// Minimal flow layout for wrapping name chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? 600
        var x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > maxWidth { x = 0; y += rowH + spacing; rowH = 0 }
            x += size.width + spacing
            rowH = max(rowH, size.height)
        }
        return CGSize(width: maxWidth, height: y + rowH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowH: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                x = bounds.minX; y += rowH + spacing; rowH = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowH = max(rowH, size.height)
        }
    }
}
