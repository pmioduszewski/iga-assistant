import Foundation

// MARK: - Read-only skill / widget discovery
//
// Scans `~/Gaia/skills/*/SKILL.md` frontmatter for two opt-in blocks:
//   * `proactive:` — the skill registers background jobs (engine territory).
//   * `widgets:`    — the skill registers one or more render-only widgets.
//
// This is the SAME SPIRIT as the engine's job discovery (engine/runtime.py
// `discover_job_sources` / `_has_proactive_block`), re-expressed app-side and
// strictly READ-ONLY: it opens SKILL.md files for reading, parses a tiny
// subset of YAML, and never writes, never execs, never decides anything.
// It powers (a) the "Skills" section (#3 — which skills feed the numbers)
// and (b) the WidgetHost's list of widgets to poll.
//
// The parser is deliberately minimal (not general YAML) — exactly enough for
// the documented `widgets:` schema, matching the Python side's "not a general
// YAML parser by design" stance.

/// One registered widget, as declared in a skill's `widgets:` frontmatter.
struct RegisteredWidget: Equatable, Identifiable {
    let skill: String          // owning skill dir name
    let id: String             // widget id (unique within the skill)
    let type: String           // "contribution-grid" | "message" | ...
    let title: String
    let dataSource: String     // resolved absolute path to the data file
    let refresh: Int           // poll seconds

    /// Stable identity for SwiftUI ForEach — skill-qualified so two skills
    /// can register a widget with the same local id without colliding.
    var uniqueKey: String { "\(skill)/\(id)" }
}

/// One discovered skill that opted into proactive jobs and/or widgets.
struct DiscoveredSkill: Equatable, Identifiable {
    let name: String           // skill dir name
    let summary: String        // plain one-liner (from `description:`)
    let hasProactive: Bool
    let widgetCount: Int

    var id: String { name }
}

enum SkillDiscovery {

    /// `~/Gaia/skills` (sibling of the iga-proactive skill dir). Honors the
    /// same layout the engine uses.
    static func skillsDir() -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent("Gaia/skills")
    }

    /// Expand a leading `~` and `~/Gaia/...` style path to absolute.
    static func expand(_ path: String) -> String {
        if path == "~" {
            return FileManager.default
                .homeDirectoryForCurrentUser.path
        }
        if path.hasPrefix("~/") {
            let home = FileManager.default
                .homeDirectoryForCurrentUser.path
            return home + "/" + String(path.dropFirst(2))
        }
        return path
    }

    // MARK: frontmatter slice

    /// Return the text between the leading `---` fences, or nil if absent.
    /// Mirrors engine/schema.py::extract_frontmatter_block (read-only).
    static func frontmatter(of skillMD: String) -> String? {
        guard skillMD.hasPrefix("---") else { return nil }
        var lines = skillMD.components(separatedBy: "\n")
        guard !lines.isEmpty, lines[0].trimmingCharacters(
            in: .whitespaces) == "---" else { return nil }
        lines.removeFirst()
        var fm: [String] = []
        for line in lines {
            if line.trimmingCharacters(in: .whitespaces) == "---" {
                return fm.joined(separator: "\n")
            }
            fm.append(line)
        }
        return nil
    }

    private static func indent(_ s: String) -> Int {
        s.prefix { $0 == " " }.count
    }

    private static func stripComment(_ s: String) -> String {
        // Drop a trailing ` # comment` (not inside quotes — our values are
        // simple scalars/paths, no inline '#').
        if let r = s.range(of: " #") {
            return String(s[..<r.lowerBound])
        }
        return s
    }

    /// True iff the frontmatter declares a top-level `proactive:` key.
    static func hasProactiveBlock(_ fm: String) -> Bool {
        for line in fm.components(separatedBy: "\n") {
            let t = stripComment(line)
            if indent(t) == 0,
               t.trimmingCharacters(in: .whitespaces)
                .hasPrefix("proactive:") {
                return true
            }
        }
        return false
    }

    /// Pull `description:` (first scalar) for the plain one-liner.
    static func description(_ fm: String) -> String {
        for line in fm.components(separatedBy: "\n") {
            let t = stripComment(line)
            if indent(t) == 0,
               t.trimmingCharacters(in: .whitespaces)
                .hasPrefix("description:") {
                let v = t.drop { $0 != ":" }.dropFirst()
                return v.trimmingCharacters(
                    in: CharacterSet(charactersIn: " \"'"))
            }
        }
        return ""
    }

    /// Parse the `widgets:` list. Minimal: a top-level `widgets:` key, then
    /// `- id: ...` list items each with flat `key: value` fields plus an
    /// optional nested `coach:` mapping (ignored for rendering — the data
    /// file carries the live coach text; the spec only names the field).
    static func widgets(
        in fm: String, skill: String, defaultDataDir: String
    ) -> [RegisteredWidget] {
        let lines = fm.components(separatedBy: "\n")
        var i = 0
        // find top-level `widgets:`
        while i < lines.count {
            let t = stripComment(lines[i])
            if indent(t) == 0,
               t.trimmingCharacters(in: .whitespaces)
                .hasPrefix("widgets:") { i += 1; break }
            i += 1
        }
        if i >= lines.count { return [] }

        var out: [RegisteredWidget] = []
        var cur: [String: String] = [:]
        var inWidgets = false
        var nestedIndent: Int? = nil

        func flush() {
            guard let id = cur["id"], !id.isEmpty else { cur = [:]; return }
            let type = cur["type"] ?? "message"
            let title = cur["title"] ?? id
            let ds = cur["data_source"].map(expand)
                ?? "\(defaultDataDir)/\(skill)-\(id).json"
            let refresh = Int(cur["refresh"] ?? "") ?? 60
            out.append(RegisteredWidget(
                skill: skill, id: id, type: type, title: title,
                dataSource: ds, refresh: max(2, refresh)))
            cur = [:]
        }

        while i < lines.count {
            let raw = stripComment(lines[i])
            i += 1
            if raw.trimmingCharacters(in: .whitespaces).isEmpty { continue }
            let ind = indent(raw)
            let stripped = raw.trimmingCharacters(in: .whitespaces)

            // Dedent back to column 0 on a non-list key → widgets block ended.
            if ind == 0 && !stripped.hasPrefix("-") { break }

            if stripped.hasPrefix("- ") {
                if inWidgets { flush() }
                inWidgets = true
                nestedIndent = nil
                let item = String(stripped.dropFirst(2))
                    .trimmingCharacters(in: .whitespaces)
                if let (k, v) = kv(item) {
                    if v.isEmpty { nestedIndent = ind + 1 }
                    else { cur[k] = v }
                }
                continue
            }
            guard inWidgets else { continue }

            // Inside a nested mapping (e.g. `coach:`): skip its children —
            // the spec's coach config doesn't change rendering (data file
            // carries the live text). Detect leaving the nested block.
            if let ni = nestedIndent, ind >= ni { continue }
            nestedIndent = nil

            if let (k, v) = kv(stripped) {
                if v.isEmpty { nestedIndent = ind + 1 }
                else { cur[k] = v }
            }
        }
        if inWidgets { flush() }
        return out
    }

    private static func kv(_ s: String) -> (String, String)? {
        guard let c = s.firstIndex(of: ":") else { return nil }
        let k = String(s[..<c]).trimmingCharacters(in: .whitespaces)
        let v = String(s[s.index(after: c)...])
            .trimmingCharacters(
                in: CharacterSet(charactersIn: " \"'"))
        if k.isEmpty { return nil }
        return (k, v)
    }

    // MARK: top-level scan

    struct ScanResult: Equatable {
        var skills: [DiscoveredSkill] = []
        var widgets: [RegisteredWidget] = []
    }

    /// Scan every `skills/*/SKILL.md` once. Read-only; never throws to the
    /// caller (an unreadable skill is simply skipped, like the engine does).
    static func scan() -> ScanResult {
        var res = ScanResult()
        let fm = FileManager.default
        let dir = skillsDir()
        let home = fm.homeDirectoryForCurrentUser.path
        let defaultDataDir = "\(home)/Gaia/state/widgets"

        guard let children = try? fm.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]) else {
            return res
        }
        for child in children.sorted(by: {
            $0.lastPathComponent < $1.lastPathComponent }) {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: child.path, isDirectory: &isDir),
                  isDir.boolValue else { continue }
            let skillName = child.lastPathComponent
            let md = child.appendingPathComponent("SKILL.md")
            guard let text = try? String(
                contentsOf: md, encoding: .utf8),
                  let block = frontmatter(of: text) else { continue }

            let hasProactive = hasProactiveBlock(block)
            let ws = widgets(
                in: block, skill: skillName,
                defaultDataDir: defaultDataDir)
            if !hasProactive && ws.isEmpty { continue }

            res.widgets.append(contentsOf: ws)
            res.skills.append(DiscoveredSkill(
                name: skillName,
                summary: plainSummary(description(block)),
                hasProactive: hasProactive,
                widgetCount: ws.count))
        }
        return res
    }

    /// Trim a skill `description:` to a short, plain first clause for the
    /// Skills list. Pure string shaping — no logic.
    static func plainSummary(_ desc: String) -> String {
        if desc.isEmpty { return "Registers proactive work." }
        // First sentence / em-dash clause, capped.
        let firstSentence: String = {
            if let r = desc.range(of: ". ") {
                return String(desc[..<r.lowerBound])
            }
            return desc
        }()
        let capped = firstSentence.count > 110
            ? String(firstSentence.prefix(108)) + "…"
            : firstSentence
        return capped
    }
}
