# Codex Backup Secret Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace only high-confidence real credentials in the Telepiplex Codex backup with `***`, rebuild the encrypted migration package, and prove that the result is complete, readable, authenticated, and tamper-resistant.

**Architecture:** A dependency-free Node.js detector discovers contextual and strongly formatted secrets, then propagates confirmed values across every text representation without logging them. A tree processor expands the three nested archives, applies text and macOS Vision-based image redaction in an isolated staging directory, rebuilds the archives, and emits a value-free report. Packaging and encryption happen in a second temporary directory; verified files replace the old deliverables only after all checks pass.

**Tech Stack:** Node.js 24 built-ins, `node:test`, macOS Swift 6 with Vision/AppKit, `/usr/bin/tar`, `/usr/bin/zip`, `/usr/bin/unzip`, LibreSSL `openssl`, SHA-256, HMAC-SHA256.

## Global Constraints

- Replace secret values with the literal `***`.
- Redact only high-confidence real credentials; preserve usernames, email addresses, phone numbers, IP addresses, hostnames, filesystem paths, thread IDs, UUIDs, ordinary hashes, Git commits, non-credential URLs, examples, placeholders, environment references, and ambiguous high-entropy strings.
- Never scan, copy, rewrite, print, or package `/Users/young/.codex/secure-keys/telepiplex-codex-context-20260720.key`; only pass its path to encryption and verification commands.
- Never modify `~/.codex` sessions, attachments, memory, login state, or databases.
- Reports and test output contain category, relative path, and counts only—never original secret values.
- A high-confidence hit that cannot be safely rewritten is a hard failure.
- Keep the current plaintext and encrypted artifacts unchanged until the staged sanitized package passes all verification.
- Final key directory mode is `700`; final key file mode is `600`; FileVault remains enabled.

---

### Task 1: High-confidence text detector

**Files:**
- Create: `tools/secret-redaction/detect-secrets.mjs`
- Create: `tools/secret-redaction/detect-secrets.test.mjs`

**Interfaces:**
- Produces: `discoverSecrets(text: string): Finding[]`
- Produces: `redactText(text: string, knownSecrets: Set<string>): { text: string, findings: PublicFinding[], discovered: Set<string> }`
- Produces: `isPlaceholder(value: string): boolean`
- `Finding` is `{ category: string, start: number, end: number, value: string, replacement: string }`.
- `PublicFinding` is `{ category: string, count: number }` and never contains `value`.

- [ ] **Step 1: Write failing detector tests**

Cover exact contextual replacements and explicit non-replacements:

```js
import assert from "node:assert/strict";
import test from "node:test";
import { discoverSecrets, isPlaceholder, redactText } from "./detect-secrets.mjs";

test("redacts high-confidence contextual credentials", () => {
  const input = [
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue123456",
    'PROWLARR_API_KEY="0123456789abcdef0123456789abcdef"',
    "postgres://worker:correct-horse-battery-staple@db.internal/app",
  ].join("\n");
  const result = redactText(input, new Set());
  assert.match(result.text, /Authorization: Bearer \*\*\*/);
  assert.match(result.text, /PROWLARR_API_KEY="\*\*\*"/);
  assert.match(result.text, /postgres:\/\/worker:\*\*\*@db\.internal/);
  assert.equal(result.discovered.size, 3);
});

test("preserves examples and non-secret identifiers", () => {
  const input = [
    "TOKEN=${TOKEN}",
    "password=changeme",
    "commit=4d40956f00aa11bb22cc33dd44ee55ff66aa77bb",
    "thread_id=019f7eb3-abc8-7b80-b1ef-904f8731a733",
    "email=young@example.com",
    "path=/Users/young/Documents/telepiplex",
  ].join("\n");
  assert.equal(redactText(input, new Set()).text, input);
});

test("propagates only confirmed values", () => {
  const secret = "0123456789abcdef0123456789abcdef";
  const result = redactText(`API_KEY=${secret}\nmessage=${secret}`, new Set());
  assert.equal(result.text, "API_KEY=***\nmessage=***");
});

test("recognizes placeholders", () => {
  for (const value of ["***", "<TOKEN>", "${TOKEN}", "example", "changeme", "redacted"]) {
    assert.equal(isPlaceholder(value), true);
  }
});
```

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run:

```bash
node --test tools/secret-redaction/detect-secrets.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `detect-secrets.mjs`.

- [ ] **Step 3: Implement the minimal detector**

Implement ordered rules for:

```js
const sensitiveField =
  "(?:password|passwd|pwd|passphrase|api[_-]?key|client[_-]?secret|" +
  "access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?secret|" +
  "webhook[_-]?secret|private[_-]?key|secret[_-]?key|x[-_]?plex[-_]?token)";

const placeholders = [
  /^\*+$/i,
  /^<[^>]+>$/i,
  /^\$\{[^}]+\}$/i,
  /^(?:example|sample|dummy|test|fake|placeholder|redacted|changeme|null|none)$/i,
  /^(?:your|replace[-_ ]?me)(?:[-_ ].*)?$/i,
  /(?:\.\.\.|…)/,
];
```

Add contextual matchers for JSON string fields, environment/shell assignments, CLI flags, Authorization headers, authentication/session cookies, credential-bearing URLs, and sensitive URL query parameters. Add strong standalone matchers for private-key PEM blocks, JWTs, OpenAI, GitHub, Slack, AWS, Google, Stripe, Discord, and Telegram credential formats. Require non-placeholder values and contextual minimum lengths; do not use a generic hexadecimal or generic entropy matcher.

Build replacements from match offsets, sort non-overlapping findings from the end of the string, and add confirmed values of at least eight characters to `knownSecrets`. After contextual replacement, replace every exact occurrence of each known value with `***`. Public results aggregate category counts and discard raw values.

- [ ] **Step 4: Run detector tests**

Run:

```bash
node --test tools/secret-redaction/detect-secrets.test.mjs
```

Expected: 4 tests pass, 0 fail.

- [ ] **Step 5: Commit detector and tests**

```bash
git add tools/secret-redaction/detect-secrets.mjs tools/secret-redaction/detect-secrets.test.mjs
git commit -m "feat: add high-confidence secret detector"
```

### Task 2: Safe tree and nested-archive processor

**Files:**
- Create: `tools/secret-redaction/redact-backup.mjs`
- Create: `tools/secret-redaction/redact-backup.test.mjs`

**Interfaces:**
- Consumes: `discoverSecrets`, `redactText` from Task 1.
- Produces: `sanitizeBackup({ sourceDir, stagingRoot, reportPath, imageHelper }): Promise<SanitizeResult>`.
- `SanitizeResult` is `{ outputDir: string, filesScanned: number, filesChanged: number, replacements: number, categories: Record<string, number>, secondScan: { findings: number } }`.
- CLI modes:
  - `node redact-backup.mjs scan <sourceDir> <reportPath>`
  - `node redact-backup.mjs sanitize <sourceDir> <stagingRoot> <reportPath> [imageHelper]`

- [ ] **Step 1: Write failing archive and propagation tests**

Create fixtures at runtime under `fs.mkdtemp()`; do not commit credential-bearing fixtures:

```js
test("sanitizes outer text and nested tar members without exposing values", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "redactor-test."));
  const source = path.join(root, "package");
  await fs.mkdir(path.join(source, "raw"), { recursive: true });
  const secret = "0123456789abcdef0123456789abcdef";
  await fs.writeFile(path.join(source, "README.md"), `API_KEY=${secret}\n`);
  const archiveSource = path.join(root, "archive-source");
  await fs.mkdir(path.join(archiveSource, "sessions"), { recursive: true });
  await fs.writeFile(
    path.join(archiveSource, "sessions", "a.jsonl"),
    `${JSON.stringify({ payload: { message: secret } })}\n`,
  );
  assert.equal(
    spawnSync(
      "/usr/bin/tar",
      ["-czf", path.join(source, "raw", "codex-sessions.tar.gz"), "-C", archiveSource, "sessions"],
      { encoding: "utf8" },
    ).status,
    0,
  );
  const result = await sanitizeBackup({
    sourceDir: source,
    stagingRoot: path.join(root, "stage"),
    reportPath: path.join(root, "report.json"),
  });
  assert.equal(result.replacements, 2);
  assert.doesNotMatch(await fs.readFile(path.join(root, "report.json"), "utf8"), new RegExp(secret));
});

test("rejects archive traversal", async () => {
  assert.throws(() => validateArchiveMembers(["../escape"]), /unsafe archive member/);
  assert.throws(() => validateArchiveMembers(["/absolute"]), /unsafe archive member/);
});
```

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run:

```bash
node --test tools/secret-redaction/redact-backup.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `redact-backup.mjs`.

- [ ] **Step 3: Implement safe staging and archive handling**

Implement these exact safeguards:

```js
export function validateArchiveMembers(members) {
  for (const member of members) {
    if (path.isAbsolute(member) || member.split("/").includes("..")) {
      throw new Error(`unsafe archive member: ${path.basename(member)}`);
    }
  }
}
```

Use `fs.realpath()` and `path.relative()` to guarantee source, staging, extraction, and output paths stay under their declared roots. Reject symlinks. Copy with `fs.cp({ recursive: true, preserveTimestamps: true })`. Treat valid UTF-8 files without NUL bytes as text; preserve their original mode after atomic sibling-file replacement.

For each of these exact archives, list members with `/usr/bin/tar -tzf`, validate names, extract under `stagingRoot/unpacked/<name>`, run both discovery and replacement passes, then rebuild with `COPYFILE_DISABLE=1 /usr/bin/tar -czf`:

```text
raw/codex-sessions.tar.gz
raw/codex-attachments.tar.gz
raw/memory-rollout-summaries.tar.gz
```

Run discovery over all outer text, all expanded text, and OCR observations before replacement so confirmed values propagate across representations. JSON report keys are `filesScanned`, `filesChanged`, `replacements`, `categories`, and `changedFiles`; `changedFiles` contains relative paths and counts only.

- [ ] **Step 4: Run processor tests**

Run:

```bash
node --test tools/secret-redaction/redact-backup.test.mjs
```

Expected: all tests pass and temporary fixtures are removed in test cleanup.

- [ ] **Step 5: Commit processor and tests**

```bash
git add tools/secret-redaction/redact-backup.mjs tools/secret-redaction/redact-backup.test.mjs
git commit -m "feat: sanitize backup trees and nested archives"
```

### Task 3: macOS Vision image inspection and masking

**Files:**
- Create: `tools/secret-redaction/RedactImageSecrets.swift`
- Create: `tools/secret-redaction/image-redaction.test.mjs`

**Interfaces:**
- Produces CLI `RedactImageSecrets scan <input.png>` returning JSON:
  `[{ "text": string, "x": number, "y": number, "width": number, "height": number }]`.
- Produces CLI `RedactImageSecrets mask <input.png> <output.png> <rectangles.json>`.
- The Node tree processor passes recognized text through Task 1 and gives matching observation rectangles to `mask`.

- [ ] **Step 1: Write a failing image round-trip test**

The test creates a 1800×300 PNG with AppKit containing a high-confidence Authorization header, scans it, applies masking, and confirms the output opens and differs byte-for-byte from the input:

```js
test("OCR finds and masks a credential line", () => {
  const scan = spawnSync(helper, ["scan", input], { encoding: "utf8" });
  assert.equal(scan.status, 0);
  const observations = JSON.parse(scan.stdout);
  assert.ok(observations.some((item) => item.text.includes("Authorization")));
  const mask = spawnSync(helper, ["mask", input, output, rectangles], { encoding: "utf8" });
  assert.equal(mask.status, 0);
  assert.notDeepEqual(fsSync.readFileSync(output), fsSync.readFileSync(input));
});
```

- [ ] **Step 2: Run the test and confirm the expected failure**

Run:

```bash
node --test tools/secret-redaction/image-redaction.test.mjs
```

Expected: FAIL because the Swift helper binary does not exist.

- [ ] **Step 3: Implement and compile the Vision helper**

Use `VNRecognizeTextRequest` with `.accurate`, `usesLanguageCorrection = false`, and English plus Simplified Chinese recognition languages. Convert Vision’s bottom-left normalized rectangles to AppKit coordinates. For masking, draw the source image, fill each target rectangle with an opaque black rectangle with 6-pixel padding, and draw centered white `***`; write PNG through `NSBitmapImageRep`.

Compile:

```bash
mkdir -p tools/secret-redaction/bin
swiftc tools/secret-redaction/RedactImageSecrets.swift \
  -framework Vision -framework AppKit \
  -o tools/secret-redaction/bin/RedactImageSecrets
```

- [ ] **Step 4: Run image tests**

Run:

```bash
node --test tools/secret-redaction/image-redaction.test.mjs
```

Expected: all tests pass. Test logs contain no credential string.

- [ ] **Step 5: Commit source and tests, excluding the compiled binary**

```bash
git add tools/secret-redaction/RedactImageSecrets.swift tools/secret-redaction/image-redaction.test.mjs
git commit -m "feat: inspect and mask secrets in backup images"
```

### Task 4: End-to-end sanitizer verification

**Files:**
- Modify: `tools/secret-redaction/redact-backup.test.mjs`
- Modify: `tools/secret-redaction/detect-secrets.test.mjs`

**Interfaces:**
- Consumes all interfaces from Tasks 1–3.
- Produces a tested CLI that returns exit code `0` only if a second scan has zero findings.

- [ ] **Step 1: Add a failing end-to-end test**

Build a fixture containing outer Markdown, nested JSONL, nested pasted text, a memory summary, and a PNG. Include one repeated confirmed credential and examples that must survive. Assert:

```js
assert.equal(secondScan.replacements, 0);
assert.equal(result.secondScan.findings, 0);
assert.equal(JSON.parse(jsonlLine).payload.message.includes("***"), true);
assert.equal(sanitizedReadme.includes("changeme"), true);
assert.equal(sanitizedReadme.includes("/Users/young/Documents/telepiplex"), true);
assert.equal(reportText.includes(secret), false);
```

- [ ] **Step 2: Run the end-to-end test and observe the first unmet assertion**

Run:

```bash
node --test tools/secret-redaction/*.test.mjs
```

Expected: FAIL because `result.secondScan` is not yet returned by the sanitizer.

- [ ] **Step 3: Add the required propagation and rescan result**

Make the CLI:

1. discover across outer text, expanded archives, and OCR text;
2. replace contextual findings and confirmed exact values;
3. rebuild nested archives;
4. scan the rebuilt tree again;
5. exit nonzero when any high-confidence finding remains.

Do not add generic entropy detection, fuzzy matching, or redaction of non-secret personal data.

- [ ] **Step 4: Run the complete test suite**

Run:

```bash
node --test tools/secret-redaction/*.test.mjs
```

Expected: all tests pass, 0 fail.

- [ ] **Step 5: Commit end-to-end behavior**

```bash
git add tools/secret-redaction
git commit -m "test: verify backup redaction end to end"
```

### Task 5: Sanitize the real backup in isolated staging

**Files:**
- Read: `dist/telepiplex-codex-context-20260720/**`
- Create temporarily: `/tmp/telepiplex-redaction.<random>/**`
- Do not modify yet: `dist/telepiplex-codex-context-20260720/**`

**Interfaces:**
- Consumes the Task 4 CLI and compiled image helper.
- Produces staged sanitized directory and a value-free JSON report.

- [ ] **Step 1: Create a mode-700 staging directory**

Run:

```bash
umask 077
TELEPIPLEX_REDACTION_STAGE="$(mktemp -d /tmp/telepiplex-redaction.XXXXXX)"
chmod 700 "$TELEPIPLEX_REDACTION_STAGE"
printf '%s\n' "$TELEPIPLEX_REDACTION_STAGE"
```

Record the printed explicit path and confirm it begins with `/tmp/telepiplex-redaction.`. The task-specific variable is used for non-destructive staging commands; cleanup uses the printed explicit path after a separate validation.

- [ ] **Step 2: Run the sanitizer against the plaintext backup**

Run with the recorded explicit staging path:

```bash
node tools/secret-redaction/redact-backup.mjs sanitize \
  /Users/young/Documents/telepiplex/dist/telepiplex-codex-context-20260720 \
  "$TELEPIPLEX_REDACTION_STAGE/stage" \
  "$TELEPIPLEX_REDACTION_STAGE/report.json" \
  /Users/young/Documents/telepiplex/tools/secret-redaction/bin/RedactImageSecrets
```

Expected: exit `0`; output contains counts only.

- [ ] **Step 3: Review the value-free report**

Use Node to print only `filesScanned`, `filesChanged`, `replacements`, `categories`, and the relative filenames. Confirm no report property can hold a secret value.

- [ ] **Step 4: Run the second scan**

```bash
node tools/secret-redaction/redact-backup.mjs scan \
  "$TELEPIPLEX_REDACTION_STAGE/stage/telepiplex-codex-context-20260720" \
  "$TELEPIPLEX_REDACTION_STAGE/second-scan.json"
```

Expected: exit `0`, `replacements: 0`, no high-confidence findings.

- [ ] **Step 5: Validate structure before packaging**

Parse every staged JSON and every line of staged JSONL with Node. Run `tar -tzf` on all three rebuilt archives. Confirm exactly 123 raw session JSONL members, 45 pasted-text attachment files, 23 memory summaries, 123 readable transcript Markdown files, and two external PNG files. Open every changed PNG with `sips -g pixelWidth -g pixelHeight`.

### Task 6: Package, encrypt, verify, replace, and clean

**Files:**
- Replace after verification: `dist/telepiplex-codex-context-20260720.zip.aes256`
- Replace after verification: `dist/telepiplex-codex-context-20260720.zip.aes256.hmac`
- Replace after verification: `dist/telepiplex-codex-context-20260720.zip.aes256.plaintext.sha256`
- Replace after verification: `dist/telepiplex-codex-context-20260720.zip.aes256.sha256`
- Preserve: `dist/decrypt-telepiplex-codex-context.mjs`
- Preserve: `dist/TELEPIPLEX-CONTEXT-DECRYPT.md`
- Preserve: `/Users/young/.codex/secure-keys/telepiplex-codex-context-20260720.key`

**Interfaces:**
- Consumes the staged sanitized backup from Task 5.
- Produces the final encrypted migration package and no retained plaintext backup.

- [ ] **Step 1: Build and test a staged ZIP**

From the staging package parent, run `COPYFILE_DISABLE=1 /usr/bin/zip -qry` with the exact top-level directory name. Run:

```bash
/usr/bin/unzip -t "$TELEPIPLEX_REDACTION_STAGE/telepiplex-codex-context-20260720.zip"
```

Expected: `No errors detected in compressed data`.

- [ ] **Step 2: Encrypt and write staged integrity files**

Use the existing key path only as an OpenSSL/HMAC input. Encrypt with:

```bash
openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -md sha512 \
  -pass file:/Users/young/.codex/secure-keys/telepiplex-codex-context-20260720.key \
  -in "$TELEPIPLEX_REDACTION_STAGE/telepiplex-codex-context-20260720.zip" \
  -out "$TELEPIPLEX_REDACTION_STAGE/telepiplex-codex-context-20260720.zip.aes256"
```

Generate ciphertext HMAC with the second key-file line inside a Node process without printing the key. Generate staged plaintext and ciphertext SHA-256 sidecars with basenames matching the final deliverables.

- [ ] **Step 3: Perform the real decryption loop**

Run the existing decrypt helper on the staged encrypted file. Confirm:

- HMAC passes;
- decrypted SHA-256 equals the staged plaintext SHA-256;
- `cmp -s` reports byte-identical ZIPs;
- `unzip -t` succeeds.

- [ ] **Step 4: Prove tamper rejection**

Copy the staged ciphertext and sidecars to an explicit temporary tamper filename, flip byte 64 with a Node one-liner, and run the decrypt helper. Expected: nonzero exit with `HMAC verification failed` and no plaintext output file.

- [ ] **Step 5: Verify permissions and disk encryption**

Run:

```bash
stat -f '%Sp %OLp %N' \
  /Users/young/.codex/secure-keys \
  /Users/young/.codex/secure-keys/telepiplex-codex-context-20260720.key
/usr/bin/fdesetup status
```

Expected: directory `700`, file `600`, and `FileVault is On.`

- [ ] **Step 6: Atomically replace final encrypted deliverables**

For each of the four staged files, copy to a mode-600 sibling temporary file in `dist/`, `fsync` it, then rename it over the exact final path. Re-run ciphertext SHA-256 verification and one final decrypt-helper round trip against the final paths before cleanup.

- [ ] **Step 7: Remove only verified plaintext and explicit temporary paths**

After final verification, delete these exact plaintext artifacts:

```text
/Users/young/Documents/telepiplex/dist/telepiplex-codex-context-20260720
/Users/young/Documents/telepiplex/dist/telepiplex-codex-context-20260720.zip
/Users/young/Documents/telepiplex/dist/telepiplex-codex-context-20260720.zip.sha256
```

Validate the recorded staging path before deletion:

```bash
case "$TELEPIPLEX_REDACTION_STAGE" in
  /tmp/telepiplex-redaction.*) test -d "$TELEPIPLEX_REDACTION_STAGE" ;;
  *) exit 1 ;;
esac
```

Then replace the variable with the printed absolute path in the deletion command. Delete that one explicit directory and `/tmp/telepiplex-encrypted-verify.aGw1mZ` if it still exists. Do not delete any path under `~/.codex`; the key and all original Codex data remain.

- [ ] **Step 8: Final artifact audit**

List the six retained `dist/` deliverables, verify that no plaintext backup directory or ZIP remains, and run `git status --short`. Report replacement counts by category and filenames without revealing secret values.
