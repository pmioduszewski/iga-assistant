import XCTest
@testable import IgaMenuBar

// MARK: - Defect #1, MACHINE-CHECKED: Grid is a read-only contribution chart
//
// History: the dense grid regressed THREE times — bad "fill the width" specs
// satisfied by ballooning the inter-column gap to ~65pt → huge scattered
// squares. The model is now exactly GitHub-style contribution chart:
// FIXED 7pt cells, FIXED 2pt gaps, 7 weekday rows, columns = weeks over the
// FULL decoded history, ALWAYS in a horizontal ScrollView anchored to the
// trailing (newest) edge, with a thin month-label header. There is NO period
// selector and Grid is READ-ONLY (no Button / onTapGesture / relay).
//
// This suite is deliberately NOT gameable by a stretch/scatter layout: it
// asserts the render gap is exactly `denseGap` (==2) and the rendered width
// equals the natural width (never stretched); that a longer history adds
// COLUMNS not rows (rows always 7); that `monthLabelColumns` labels exactly
// the month-change columns; and — via a source grep mirroring ContractLitmus
// — that the dense/Grid render path contains NO mutator, while Compact still
// owns the record seam. Pure functions, no running UI.

final class DenseGridLayoutTests: XCTestCase {

    private let availableWidth: CGFloat = 360

    /// Build `days` consecutive decoded cells starting at a fixed date, then
    /// bucket them with the production `weekColumns` so N matches the live
    /// layout exactly (full history — no period gating anymore).
    private func cells(forDays days: Int) -> [GridCell] {
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let start = DateComponents(
            calendar: cal, year: 2025, month: 1, day: 1).date!
        let fmt = DateFormatter()
        fmt.calendar = cal
        fmt.timeZone = TimeZone(identifier: "UTC")
        fmt.dateFormat = "yyyy-MM-dd"
        return (0..<days).map { i in
            let d = cal.date(byAdding: .day, value: i, to: start)!
            return GridCell(date: fmt.string(from: d), level: i % 5)
        }
    }

    private func columns(forDays days: Int) -> Int {
        HabitsWidgetView.weekColumns(cells(forDays: days)).count
    }

    private func metrics(forDays days: Int)
        -> HabitsWidgetView.DenseGridMetrics {
        HabitsWidgetView.denseGridMetrics(
            availableWidth: availableWidth,
            columns: columns(forDays: days))
    }

    // FIXED small cell + FIXED small gap for EVERY history length.
    func testCellAndGapAreFixedSmallConstants() {
        for days in [30, 90, 365, 900] {
            let m = metrics(forDays: days)
            XCTAssertEqual(m.cell, 7, accuracy: 0.0001,
                "\(days)d: cell must be the fixed 7pt (got \(m.cell))")
            XCTAssertEqual(m.gap, 2, accuracy: 0.0001,
                "\(days)d: gap must be the fixed 2pt (got \(m.gap))")
            XCTAssertEqual(
                m.cell, HabitsWidgetView.denseCell, accuracy: 0.0001)
            XCTAssertEqual(
                m.gap, HabitsWidgetView.denseGap, accuracy: 0.0001)
        }
        XCTAssertEqual(HabitsWidgetView.denseCell, 7, accuracy: 0.0001)
        XCTAssertEqual(HabitsWidgetView.denseGap, 2, accuracy: 0.0001)
        XCTAssertEqual(HabitsWidgetView.denseRows, 7)
    }

    // THE non-gameable assertion: there is NO separate render gap, and the
    // grid is NEVER stretched. The stretch/scatter regression set this ~65.
    func testRenderGapEqualsGapAndWidthIsNaturalNeverStretched() {
        for days in [30, 90, 365, 900] {
            let m = metrics(forDays: days)
            XCTAssertEqual(m.renderGap, m.gap, accuracy: 0.0001,
                "\(days)d: renderGap must equal gap (==2). A value > gap "
                + "is the stretch/scatter regression (got \(m.renderGap)).")
            XCTAssertEqual(m.renderedWidth, m.naturalWidth, accuracy: 0.0001,
                "\(days)d: rendered width must equal natural width — the "
                + "grid is NEVER stretched to the available width")
        }
    }

    // Fixed, SHORT height for every history: 7*7 + 6*2 == 61, always ≤ 70.
    func testHeightIsFixed61AndAlwaysShort() {
        for days in [30, 90, 365, 900] {
            let m = metrics(forDays: days)
            XCTAssertEqual(
                m.blockHeight, m.cell * 7 + m.gap * 6, accuracy: 0.0001,
                "\(days)d: height must be exactly 7*cell + 6*gap")
            XCTAssertEqual(m.blockHeight, 61, accuracy: 0.0001,
                "\(days)d: fixed 7/2 model → blockHeight is always 61")
            XCTAssertLessThanOrEqual(m.blockHeight, 70,
                "\(days)d: dense block must stay short (≤ 70)")
        }
        XCTAssertEqual(
            HabitsWidgetView.denseCell * 7
                + HabitsWidgetView.denseGap * 6,
            61, accuracy: 0.0001)
    }

    // A longer history adds week-COLUMNS (horizontal), never rows. Feeding N
    // days yields ceil((N + weekdayPad)/7) columns via the real weekColumns.
    func testLongerHistoryAddsColumnsNotRows() {
        XCTAssertGreaterThan(columns(forDays: 900), columns(forDays: 365))
        XCTAssertGreaterThan(columns(forDays: 365), columns(forDays: 90))
        XCTAssertGreaterThan(columns(forDays: 90), columns(forDays: 30),
            "a longer history must add week-COLUMNS, never rows")
        // Every column is still ≤ 7 rows tall regardless of history.
        for days in [30, 90, 365, 900] {
            for c in HabitsWidgetView.weekColumns(cells(forDays: days)) {
                XCTAssertLessThanOrEqual(c.count, 7,
                    "\(days)d: every week column must be ≤ 7 rows tall")
            }
        }
        // Column count tracks ceil((N + weekdayPad)/7) — the structural
        // identity that proves growth is horizontal only. 2025-01-01 is a
        // Wednesday → ISO Mon=0…Sun=6 weekday index 2, so pad == 2.
        for days in [30, 90, 365, 900] {
            let pad = HabitsWidgetView.isoWeekdayIndex("2025-01-01")!
            let expected = Int(ceil(Double(days + pad) / 7.0))
            XCTAssertEqual(columns(forDays: days), expected,
                "\(days)d: columns must be ceil((N + weekdayPad)/7)")
        }
    }

    // monthLabelColumns: a ≥3-month history labels EXACTLY the columns where
    // the month changes (first dated month included), others absent, and the
    // abbrevs are the locale short-month symbols.
    func testMonthLabelColumnsLabelsExactlyMonthChanges() {
        // 120 consecutive days from 2025-01-01 spans Jan→May 2025.
        let cols = HabitsWidgetView.weekColumns(cells(forDays: 120))
        let labels = HabitsWidgetView.monthLabelColumns(cols)
        let sym = DateFormatter().shortMonthSymbols!   // locale-aware

        // For each column, compute the month of its first dated cell. The
        // expected labelled columns are exactly those whose month differs
        // from the previous column's first-dated month (first one included).
        var expected: [Int: String] = [:]
        var prevKey: Int? = nil
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let parser = DateFormatter()
        parser.calendar = cal
        parser.timeZone = TimeZone(identifier: "UTC")
        parser.dateFormat = "yyyy-MM-dd"
        for (idx, week) in cols.enumerated() {
            guard let dated = week.first(where: {
                $0.level >= 0 && !$0.date.isEmpty
            }), let d = parser.date(from: dated.date) else { continue }
            let c = cal.dateComponents([.year, .month], from: d)
            let key = c.year! * 12 + c.month!
            if prevKey != key {
                expected[idx] = sym[c.month! - 1]
                prevKey = key
            }
        }

        XCTAssertEqual(labels, expected,
            "month labels must mark exactly the month-change columns")
        // Spot the substance: ≥ 4 distinct month labels over Jan→May, the
        // very first dated column is labelled, and a clearly mid-month
        // column with no change is absent.
        XCTAssertGreaterThanOrEqual(Set(labels.values).count, 4,
            "120d Jan→May must surface ≥ 4 distinct month abbrevs")
        XCTAssertEqual(labels[0], sym[0],
            "the first column (Jan 2025) must be labelled 'Jan'")
        XCTAssertTrue(labels.values.contains(sym[1]), "Feb labelled")
        XCTAssertTrue(labels.values.contains(sym[2]), "Mar labelled")
        // Empty input → empty mapping (pure, total).
        XCTAssertEqual(HabitsWidgetView.monthLabelColumns([]), [:])
    }

    // READ-ONLY assertion (mirrors ContractLitmus source-grep style): the
    // dense/Grid render path in HabitsWidgetView contains NO Button,
    // onTapGesture, relayToggle, or runRecord; the Compact path still relays.
    func testGridRenderPathIsReadOnlyCompactIsTheOnlyMutator() throws {
        let dir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()      // IgaMenuBarTests
            .deletingLastPathComponent()      // Tests
            .deletingLastPathComponent()      // app
            .appendingPathComponent("Sources/IgaMenuBar")
        let src = try String(
            contentsOf: dir.appendingPathComponent(
                "HabitsWidgetView.swift"), encoding: .utf8)
        let code = Self.stripComments(src)

        // Slice the file into top-level `func`/`var` member bodies by name
        // so we can grep per render path without comment noise.
        let members = Self.memberBodies(code)
        XCTAssertFalse(members.isEmpty, "could not slice member bodies")

        let mutators = ["relayToggle", "runRecord",
                        "onTapGesture", "Button("]

        // Every Grid-path member (dense*, monthLabelColumns) is READ-ONLY.
        let gridMembers = members.filter {
            $0.name.hasPrefix("dense") || $0.name == "monthLabelColumns"
        }
        XCTAssertGreaterThanOrEqual(gridMembers.count, 4,
            "expected the dense* render path to exist as several members")
        for m in gridMembers {
            for tok in mutators {
                XCTAssertFalse(m.body.contains(tok),
                    "Grid render member `\(m.name)` contains '\(tok)' — "
                    + "Grid is READ-ONLY; marking lives in Compact only")
            }
        }

        // The Compact interactive cell renderer still owns the seam.
        let compactSquare = members.first { $0.name == "square" }
        XCTAssertNotNil(compactSquare,
            "the interactive Compact `square(...)` must still exist")
        XCTAssertTrue(
            compactSquare!.body.contains("onTapGesture")
                && compactSquare!.body.contains("relayToggle"),
            "Compact's `square` must still relay clicks via the seam")

        // The read-only Grid cell renderer must NOT relay.
        let denseSquare = members.first { $0.name == "denseSquare" }
        XCTAssertNotNil(denseSquare,
            "the read-only Grid `denseSquare(...)` must exist")
        XCTAssertFalse(
            denseSquare!.body.contains("onTapGesture")
                || denseSquare!.body.contains("relayToggle"),
            "`denseSquare` must be non-interactive (no gesture/relay)")

        // Refined contract: Grid may expose a SETTINGS cog (opens the
        // manage sheet) — that is NOT marking. It's factored into the
        // shared `settingsCog` (not a `dense*` member, so the read-only
        // grep above stays meaningful) and must itself NOT relay/mark.
        let cog = members.first { $0.name == "settingsCog" }
        XCTAssertNotNil(cog, "shared `settingsCog` must exist")
        XCTAssertFalse(
            cog!.body.contains("relayToggle")
                || cog!.body.contains("runRecord"),
            "settingsCog opens the sheet only — it must NOT mark/relay")
        XCTAssertTrue(cog!.body.contains("manageTarget"),
            "settingsCog must open the manage sheet")
        let denseRow = members.first { $0.name == "denseRow" }
        XCTAssertTrue(
            denseRow?.body.contains("settingsCog") ?? false,
            "Grid rows must expose settings via the shared cog")

        // The period selector is gone entirely.
        XCTAssertFalse(code.contains("periodSelector"),
            "the dense period selector must be removed")
        XCTAssertFalse(code.contains("densePeriodDays"),
            "Grid must no longer reference densePeriodDays (full history)")
        // Grid uses an explicit horizontal ScrollView (always scrollable).
        XCTAssertTrue(code.contains("ScrollView(.horizontal"),
            "Grid must always use a horizontal ScrollView")
        XCTAssertTrue(code.contains("defaultScrollAnchor(.trailing)"),
            "Grid must anchor to the trailing edge (newest columns first)")
    }

    // MARK: - tiny source slicers (test-local, mirror ContractLitmus style)

    /// Strip `//` line and `/* */` block comments so greps assert on CODE.
    private static func stripComments(_ src: String) -> String {
        var out = ""
        out.reserveCapacity(src.count)
        var i = src.startIndex
        var inBlock = false
        while i < src.endIndex {
            let c = src[i]
            let next = src.index(after: i)
            if inBlock {
                if c == "*", next < src.endIndex, src[next] == "/" {
                    inBlock = false
                    i = src.index(after: next)
                    continue
                }
                i = next
                continue
            }
            if c == "/", next < src.endIndex, src[next] == "/" {
                while i < src.endIndex, src[i] != "\n" {
                    i = src.index(after: i)
                }
                continue
            }
            if c == "/", next < src.endIndex, src[next] == "*" {
                inBlock = true
                i = src.index(after: next)
                continue
            }
            out.append(c)
            i = next
        }
        return out
    }

    /// Slice comment-stripped Swift into (name, body) for each top-level
    /// `func <name>` / `var <name>` declaration, body = text from the decl
    /// up to (but excluding) the next sibling declaration. Coarse but
    /// sufficient to grep one render path without bleeding into another.
    private static func memberBodies(
        _ code: String
    ) -> [(name: String, body: String)] {
        let lines = code.components(separatedBy: "\n")
        var members: [(name: String, body: String, start: Int)] = []
        for (i, raw) in lines.enumerated() {
            let l = raw.trimmingCharacters(in: .whitespaces)
            // A declaration line: starts with an access/decl keyword and
            // declares a `func` or `var`. (Indented one level — these are
            // the type's members, not locals; locals are deeper-indented
            // and never start a trimmed line with `func `/`var ` here.)
            let isDecl =
                (l.hasPrefix("func ") || l.hasPrefix("private func ")
                 || l.hasPrefix("static func ")
                 || l.hasPrefix("nonisolated static func ")
                 || l.hasPrefix("private static func ")
                 || l.hasPrefix("var ") || l.hasPrefix("private var ")
                 || l.hasPrefix("nonisolated static let ")
                 || l.hasPrefix("static let "))
            guard isDecl else { continue }
            guard let kwRange = l.range(of: "func ")
                ?? l.range(of: "var ")
                ?? l.range(of: "let ") else { continue }
            let after = l[kwRange.upperBound...]
            let name = after.prefix {
                $0.isLetter || $0.isNumber || $0 == "_"
            }
            guard !name.isEmpty else { continue }
            members.append((String(name), "", i))
        }
        var out: [(name: String, body: String)] = []
        for (idx, m) in members.enumerated() {
            let end = idx + 1 < members.count
                ? members[idx + 1].start : lines.count
            let body = lines[m.start..<end].joined(separator: "\n")
            out.append((m.name, body))
        }
        return out
    }
}
