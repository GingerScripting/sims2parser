import SwiftUI

/// Builds an "hourglass" family chart centred on one sim: two generations up
/// (parents, grandparents), the sim's own generation (siblings + spouses), and
/// two generations down (children, grandchildren).
///
/// Nieces/nephews and aunts/uncles are deliberately left out — including them
/// makes the chart wider than it is useful, and clicking any box re-roots the
/// whole tree on that sim, which reaches them in one step.
///
/// Everything here joins on sim ids, never on names: a hood routinely contains
/// several sims sharing a full name, so a name-keyed graph invents edges.
enum FamilyTree {

    // MARK: - Geometry

    enum Metrics {
        /// Wide enough for the long premade surnames (Crumplebottom, Ottomas)
        /// at the 12pt name font without truncating.
        static let nodeW: CGFloat = 174
        static let nodeH: CGFloat = 58
        /// Between the two halves of a couple.
        static let coupleGap: CGFloat = 16
        /// Between neighbouring units in a band.
        static let unitGap: CGFloat = 26
        /// Vertical distance between band baselines.
        static let bandGap: CGFloat = 104
        static let padding: CGFloat = 40
    }

    // MARK: - Output

    struct Node: Identifiable {
        enum Role {
            case ego, spouse, parent, grandparent, sibling, siblingSpouse
            case child, childSpouse, grandchild, grandchildSpouse
        }
        let id: Int          // sim nid
        let sim: Sim
        let role: Role
        let frame: CGRect

        var isEgo: Bool { role == .ego }
    }

    struct Link {
        enum Kind {
            case marriage
            /// Two people drawn side by side who share a child but are not
            /// married to each other — a divorce, a widowing, or Bella Goth.
            case coparent
            case descent
        }
        let kind: Kind
        let from: CGPoint
        let to: CGPoint
    }

    struct Chart {
        var nodes: [Node] = []
        var links: [Link] = []
        var size: CGSize = .zero
        var isEmpty: Bool { nodes.count <= 1 }
    }

    // MARK: - Internal layout unit

    /// One or two sims drawn side by side (a sim and, if any, their spouse).
    private struct Unit {
        var primary: Sim
        var partner: Sim?
        var primaryRole: Node.Role
        var partnerRole: Node.Role
        /// Units hanging below this one (children of `primary`).
        var below: [Unit] = []
        /// Width of this box pair alone.
        var ownWidth: CGFloat {
            partner == nil ? Metrics.nodeW : Metrics.nodeW * 2 + Metrics.coupleGap
        }
        /// Width of this unit including everything stacked beneath it.
        var spanWidth: CGFloat {
            let below = Unit.rowWidth(below)
            return max(ownWidth, below)
        }
        static func rowWidth(_ units: [Unit]) -> CGFloat {
            guard !units.isEmpty else { return 0 }
            return units.reduce(0) { $0 + $1.spanWidth } + CGFloat(units.count - 1) * Metrics.unitGap
        }
    }

    // MARK: - Build

    static func chart(for ego: Sim, in hood: Hood) -> Chart {
        let byID = Dictionary(hood.sims.map { ($0.nid, $0) }, uniquingKeysWith: { a, _ in a })

        func sim(_ nid: Int?) -> Sim? {
            guard let nid else { return nil }
            return byID[nid]
        }
        /// Resolve a tie id list, dropping dangling ids, self-references and
        /// duplicates. Save files do contain all three.
        ///
        /// This filters against a *snapshot* of the placed set, so every loop
        /// below must re-check `placed` as it goes: a sim can be reachable by
        /// two routes at once and get claimed part-way through the iteration.
        func sims(_ nids: [Int]?, excluding: Set<Int>) -> [Sim] {
            var seen = excluding
            var out: [Sim] = []
            for nid in nids ?? [] {
                guard !seen.contains(nid), let s = byID[nid] else { continue }
                seen.insert(nid)
                out.append(s)
            }
            return out
        }

        var chart = Chart()
        // Guards against a sim appearing twice in the chart (e.g. cousin
        // marriages, or a sim who is both sibling and spouse in a tangled hood).
        var placed: Set<Int> = [ego.nid]

        // --- descendants: children, each with their own children -------------
        var childUnits: [Unit] = []
        for child in sims(ego.childrenNids, excluding: placed) {
            guard !placed.contains(child.nid) else { continue }
            placed.insert(child.nid)
            let spouse = sim(child.spouseNid).flatMap { placed.contains($0.nid) ? nil : $0 }
            if let spouse { placed.insert(spouse.nid) }
            var unit = Unit(primary: child, partner: spouse,
                            primaryRole: .child, partnerRole: .childSpouse)
            for gc in sims(child.childrenNids, excluding: placed) {
                guard !placed.contains(gc.nid) else { continue }
                placed.insert(gc.nid)
                let gcSpouse = sim(gc.spouseNid).flatMap { placed.contains($0.nid) ? nil : $0 }
                if let gcSpouse { placed.insert(gcSpouse.nid) }
                unit.below.append(Unit(primary: gc, partner: gcSpouse,
                                       primaryRole: .grandchild, partnerRole: .grandchildSpouse))
            }
            childUnits.append(unit)
        }

        // --- ego's own generation --------------------------------------------
        let egoSpouse = sim(ego.spouseNid).flatMap { placed.contains($0.nid) ? nil : $0 }
        if let egoSpouse { placed.insert(egoSpouse.nid) }
        var egoUnit = Unit(primary: ego, partner: egoSpouse,
                           primaryRole: .ego, partnerRole: .spouse)
        egoUnit.below = childUnits

        var siblingUnits: [Unit] = []
        for sib in sims(ego.siblingNids, excluding: placed) {
            // Sibling lists can include in-laws (a sibling's spouse recorded as
            // a sibling tie one-way). Whoever claimed them first keeps them —
            // showing Sara beside her husband beats showing her twice.
            guard !placed.contains(sib.nid) else { continue }
            placed.insert(sib.nid)
            let spouse = sim(sib.spouseNid).flatMap { placed.contains($0.nid) ? nil : $0 }
            if let spouse { placed.insert(spouse.nid) }
            siblingUnits.append(Unit(primary: sib, partner: spouse,
                                     primaryRole: .sibling, partnerRole: .siblingSpouse))
        }

        // Split siblings either side of the ego so the chart stays balanced.
        let leftCount = siblingUnits.count / 2
        let leftSibs = Array(siblingUnits.prefix(leftCount))
        let rightSibs = Array(siblingUnits.dropFirst(leftCount))

        // --- ancestors --------------------------------------------------------
        let father = sim(ego.fatherNid).flatMap { placed.contains($0.nid) ? nil : $0 }
        if let father { placed.insert(father.nid) }
        let mother = sim(ego.motherNid).flatMap { placed.contains($0.nid) ? nil : $0 }
        if let mother { placed.insert(mother.nid) }

        // --- band vertical positions -----------------------------------------
        // Only non-empty bands consume a row, so a sim with no known parents
        // isn't drawn under two rows of blank space.
        let hasParents = father != nil || mother != nil
        var grandparentPairs: [(Sim, Sim?, Sim?)] = []   // (parent, their father, their mother)
        for parent in [father, mother].compactMap({ $0 }) {
            let gf = sim(parent.fatherNid).flatMap { placed.contains($0.nid) ? nil : $0 }
            if let gf { placed.insert(gf.nid) }
            let gm = sim(parent.motherNid).flatMap { placed.contains($0.nid) ? nil : $0 }
            if let gm { placed.insert(gm.nid) }
            if gf != nil || gm != nil { grandparentPairs.append((parent, gf, gm)) }
        }
        let hasGrandparents = !grandparentPairs.isEmpty
        let hasChildren = !childUnits.isEmpty
        let hasGrandchildren = childUnits.contains { !$0.below.isEmpty }

        var bandY: [Int: CGFloat] = [:]     // band index (-2…+2) → y of box top
        var y: CGFloat = Metrics.padding
        for band in [-2, -1, 0, 1, 2] {
            let occupied: Bool
            switch band {
            case -2: occupied = hasGrandparents
            case -1: occupied = hasParents
            case 0:  occupied = true
            case 1:  occupied = hasChildren
            default: occupied = hasGrandchildren
            }
            if occupied {
                bandY[band] = y
                y += Metrics.bandGap
            }
        }
        let contentHeight = y - Metrics.bandGap + Metrics.nodeH + Metrics.padding

        // --- horizontal placement ---------------------------------------------
        // The ego's slot must be wide enough for the descendant rows beneath it,
        // otherwise a wide set of children would run under its siblings.
        let egoSlotW = egoUnit.spanWidth

        /// Emit the two boxes of a unit centred on `centerX`, plus their
        /// marriage link, and return the primary's centre x.
        @discardableResult
        func emit(_ unit: Unit, centerX: CGFloat, band: Int) -> CGFloat {
            guard let top = bandY[band] else { return centerX }
            let hasPartner = unit.partner != nil
            let primaryX = hasPartner
                ? centerX - (Metrics.nodeW + Metrics.coupleGap) / 2
                : centerX
            chart.nodes.append(Node(
                id: unit.primary.nid, sim: unit.primary, role: unit.primaryRole,
                frame: CGRect(x: primaryX - Metrics.nodeW / 2, y: top,
                              width: Metrics.nodeW, height: Metrics.nodeH)))
            if let partner = unit.partner {
                let partnerX = centerX + (Metrics.nodeW + Metrics.coupleGap) / 2
                chart.nodes.append(Node(
                    id: partner.nid, sim: partner, role: unit.partnerRole,
                    frame: CGRect(x: partnerX - Metrics.nodeW / 2, y: top,
                                  width: Metrics.nodeW, height: Metrics.nodeH)))
                // Parents are paired because they share a child, which is not
                // the same as being married — only claim a marriage when the
                // save actually records the spouse tie.
                let married = unit.primary.spouseNid == partner.nid
                    || partner.spouseNid == unit.primary.nid
                chart.links.append(Link(
                    kind: married ? .marriage : .coparent,
                    from: CGPoint(x: primaryX + Metrics.nodeW / 2, y: top + Metrics.nodeH / 2),
                    to: CGPoint(x: partnerX - Metrics.nodeW / 2, y: top + Metrics.nodeH / 2)))
            }
            return primaryX
        }

        /// Lay a row of units out left-to-right starting at `startX`, placing
        /// each unit's descendants beneath it.
        ///
        /// Returns each unit's *primary* box centre, not the unit centre: a
        /// descent line from the generation above has to land on the blood
        /// relative, otherwise a married child reads as though their spouse
        /// were also a child of that couple.
        @discardableResult
        func emitRow(_ units: [Unit], startX: CGFloat, band: Int) -> [CGFloat] {
            var cursor = startX
            var anchors: [CGFloat] = []
            for unit in units {
                let center = cursor + unit.spanWidth / 2
                let anchor = emit(unit, centerX: center, band: band)
                anchors.append(anchor)
                if !unit.below.isEmpty {
                    let belowW = Unit.rowWidth(unit.below)
                    let childAnchors = emitRow(unit.below,
                                               startX: center - belowW / 2,
                                               band: band + 1)
                    link(parentAnchor: anchor, unit: unit, band: band, childCenters: childAnchors)
                }
                cursor += unit.spanWidth + Metrics.unitGap
            }
            return anchors
        }

        /// Vertical drop from a couple to a horizontal bus, then down to each child.
        func link(parentAnchor: CGFloat, unit: Unit, band: Int, childCenters: [CGFloat]) {
            guard let parentTop = bandY[band], let childTop = bandY[band + 1],
                  !childCenters.isEmpty else { return }
            // Descent starts between the spouses when there are two of them.
            let originX = unit.partner == nil
                ? parentAnchor
                : parentAnchor + (Metrics.nodeW + Metrics.coupleGap) / 2
            let parentBottom = parentTop + Metrics.nodeH
            let busY = childTop - (Metrics.bandGap - Metrics.nodeH) / 2
            chart.links.append(Link(kind: .descent,
                                    from: CGPoint(x: originX, y: parentBottom),
                                    to: CGPoint(x: originX, y: busY)))
            let minX = min(childCenters.min()!, originX)
            let maxX = max(childCenters.max()!, originX)
            if maxX > minX {
                chart.links.append(Link(kind: .descent,
                                        from: CGPoint(x: minX, y: busY),
                                        to: CGPoint(x: maxX, y: busY)))
            }
            for cx in childCenters {
                chart.links.append(Link(kind: .descent,
                                        from: CGPoint(x: cx, y: busY),
                                        to: CGPoint(x: cx, y: childTop)))
            }
        }

        // Band 0, with the ego's slot centred on x = 0.
        let leftW = Unit.rowWidth(leftSibs)
        let rightW = Unit.rowWidth(rightSibs)
        let rowMin = leftSibs.isEmpty ? -egoSlotW / 2
                                      : -egoSlotW / 2 - Metrics.unitGap - leftW
        let rowMax = rightSibs.isEmpty ? egoSlotW / 2
                                       : egoSlotW / 2 + Metrics.unitGap + rightW

        // Anchors of everyone in band 0 who is a child of the parents above.
        var band0Anchors: [CGFloat] = []
        if !leftSibs.isEmpty {
            band0Anchors += emitRow(leftSibs, startX: rowMin, band: 0)
        }
        let egoAnchor = emit(egoUnit, centerX: 0, band: 0)
        band0Anchors.append(egoAnchor)
        if hasChildren {
            let childrenW = Unit.rowWidth(childUnits)
            let anchors = emitRow(childUnits, startX: -childrenW / 2, band: 1)
            link(parentAnchor: egoAnchor, unit: egoUnit, band: 0, childCenters: anchors)
        }
        if !rightSibs.isEmpty {
            band0Anchors += emitRow(rightSibs, startX: egoSlotW / 2 + Metrics.unitGap, band: 0)
        }

        // Band -1: the parents, centred over the whole sibling row.
        if hasParents {
            let parentCenter = (rowMin + rowMax) / 2
            let parentUnit = Unit(primary: father ?? mother!,
                                  partner: father == nil ? nil : mother,
                                  primaryRole: .parent, partnerRole: .parent)
            let anchor = emit(parentUnit, centerX: parentCenter, band: -1)
            link(parentAnchor: anchor, unit: parentUnit, band: -1,
                 childCenters: band0Anchors.sorted())

            // Band -2: grandparents, spread above the parent unit.
            if hasGrandparents {
                let units = grandparentPairs.map { _, gf, gm in
                    Unit(primary: gf ?? gm!, partner: gf == nil ? nil : gm,
                         primaryRole: .grandparent, partnerRole: .grandparent)
                }
                let totalW = Unit.rowWidth(units)
                var gcursor = parentCenter - totalW / 2
                var gcenters: [CGFloat] = []
                for unit in units {
                    let center = gcursor + unit.spanWidth / 2
                    emit(unit, centerX: center, band: -2)
                    gcenters.append(center)
                    gcursor += unit.spanWidth + Metrics.unitGap
                }
                // Each grandparent couple connects down to their own child.
                for (i, pair) in grandparentPairs.enumerated() {
                    guard let gTop = bandY[-2], let pTop = bandY[-1] else { break }
                    let childX = chart.nodes.first { $0.id == pair.0.nid }
                        .map { $0.frame.midX } ?? parentCenter
                    let busY = pTop - (Metrics.bandGap - Metrics.nodeH) / 2
                    chart.links.append(Link(kind: .descent,
                                            from: CGPoint(x: gcenters[i], y: gTop + Metrics.nodeH),
                                            to: CGPoint(x: gcenters[i], y: busY)))
                    chart.links.append(Link(kind: .descent,
                                            from: CGPoint(x: gcenters[i], y: busY),
                                            to: CGPoint(x: childX, y: busY)))
                    chart.links.append(Link(kind: .descent,
                                            from: CGPoint(x: childX, y: busY),
                                            to: CGPoint(x: childX, y: pTop)))
                }
            }
        }

        // --- normalise into positive coordinates -------------------------------
        let minX = chart.nodes.map(\.frame.minX).min() ?? 0
        let maxX = chart.nodes.map(\.frame.maxX).max() ?? 0
        let shift = Metrics.padding - minX
        chart.nodes = chart.nodes.map {
            Node(id: $0.id, sim: $0.sim, role: $0.role,
                 frame: $0.frame.offsetBy(dx: shift, dy: 0))
        }
        chart.links = chart.links.map {
            Link(kind: $0.kind,
                 from: CGPoint(x: $0.from.x + shift, y: $0.from.y),
                 to: CGPoint(x: $0.to.x + shift, y: $0.to.y))
        }
        chart.size = CGSize(width: maxX - minX + Metrics.padding * 2, height: contentHeight)
        return chart
    }
}
