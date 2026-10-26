package restore

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

const key = "k3y-0123456789abcdef0123456789abcdef0123456789abcdef0123456789ab"

var restoredSecrets = "GLOW_SECRET_KEY=s1\nPOSTGRES_PASSWORD=restoredpg\nODK_POSTGRES_PASSWORD=restoredodk\n" +
	"ODK_ADMIN_PASSWORD=a1\nODK_API_PASSWORD=a2\nGLOW_BACKUP_KEY=" + strings.Repeat("ab", 32) + "\n"

// fake records docker and compose calls in one ordered log.
type fake struct {
	log     *[]string
	stdins  map[string]string
	prefix  string
	failOn  string // a call containing this fails
	running string // `docker ps` output
	inspect string // `docker inspect` output (container name + mounts JSON per line)
	noSnap  []string
	version string // deployment.json version in the snapshot ("" = v1.2.3)
	secrets string // restored secrets.env ("" = restoredSecrets)
}

func (f *fake) Run(_ context.Context, args []string, stdin io.Reader) (string, error) {
	s := f.prefix + " " + strings.Join(args, " ")
	*f.log = append(*f.log, s)
	if stdin != nil {
		b, _ := io.ReadAll(stdin)
		f.stdins[s] = string(b)
	}
	if f.failOn != "" && strings.Contains(s, f.failOn) {
		return "", errors.New("boom")
	}
	switch {
	case strings.Contains(s, "check-key"):
		v := f.version
		if v == "" {
			v = "v1.2.3"
		}
		return "restic output\n{\"id\":\"abc123full\",\"time\":\"2026-10-19T10:00:00Z\",\"version\":\"" + v + "\"}\n", nil
	case strings.HasPrefix(s, "docker ps"):
		return f.running, nil
	case strings.HasPrefix(s, "docker inspect"):
		return f.inspect, nil
	case strings.Contains(s, "restore-files"):
		for i, a := range args {
			if a == "-v" && strings.HasSuffix(args[i+1], ":/staging") {
				f.writeSnapshot(strings.TrimSuffix(args[i+1], ":/staging"))
			}
		}
	}
	return "", nil
}

func (f *fake) writeSnapshot(dir string) {
	sec := f.secrets
	if sec == "" {
		sec = restoredSecrets
	}
	files := map[string]string{
		"state/secrets.env":                      sec,
		"state/odk-secrets/enketo-secret":        "restored-enketo",
		"state/vintages/20260101T000000Z/m.json": "restored-vintage",
		"state/odk-forms-state.json":             "{\"restored\":true}",
		"state/deployment.json":                  "{}",
		"release/glow.env":                       "GLOW_DOMAIN=old.example.org\n",
		"tmp/glow-dumps/glow.dump":               "g",
		"tmp/glow-dumps/odk.dump":                "o",
	}
	for p, c := range files {
		skip := false
		for _, n := range f.noSnap {
			skip = skip || strings.HasPrefix(p, n)
		}
		if skip {
			continue
		}
		os.MkdirAll(filepath.Join(dir, filepath.Dir(p)), 0o755)
		os.WriteFile(filepath.Join(dir, p), []byte(c), 0o600)
	}
}

type env struct {
	rel, st, from string
	log           []string
	docker, comp  *fake
	gets          int
	out           bytes.Buffer
	o             Options
}

func setup(t *testing.T) *env {
	t.Helper()
	e := &env{rel: t.TempDir(), st: t.TempDir(), from: t.TempDir()}
	os.WriteFile(filepath.Join(e.rel, "glow.env"), []byte("GLOW_DOMAIN=example.org\n"), 0o600)
	os.WriteFile(filepath.Join(e.rel, "VERSION"), []byte("v1.2.3\n"), 0o644)
	os.WriteFile(filepath.Join(e.st, "secrets.env"), []byte("GLOW_SECRET_KEY=live\n"), 0o600)
	os.WriteFile(filepath.Join(e.st, "runtime.env"), []byte("OLD=1\n"), 0o600)
	os.MkdirAll(filepath.Join(e.st, "vintages", "live"), 0o755)
	e.docker = &fake{log: &e.log, stdins: map[string]string{}, prefix: "docker"}
	e.comp = &fake{log: &e.log, stdins: map[string]string{}, prefix: "compose"}
	cfg := &config.Config{Domain: "example.org", TLSMode: "acme", HTTPPort: 80, HTTPSPort: 443,
		StateDir: e.st, BackupPath: "/srv/bk", SMTP: config.SMTPConfig{Port: 587}}
	e.o = Options{
		ReleaseDir: e.rel, Cfg: cfg, From: e.from, Snapshot: "latest", Key: key,
		Compose: e.comp, Docker: e.docker, Out: &e.out, Interval: 1,
		Get: func(context.Context, *config.Config, string, string) (int, error) { e.gets++; return 200, nil },
	}
	return e
}

func read(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func ls(t *testing.T, d string) []string {
	t.Helper()
	es, _ := os.ReadDir(d)
	var n []string
	for _, x := range es {
		n = append(n, x.Name())
	}
	return n
}

func TestWrongKeyTouchesNothing(t *testing.T) {
	e := setup(t)
	e.docker.failOn = "check-key"
	before := ls(t, e.st)
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "backup key") {
		t.Fatalf("want key error, got %v", err)
	}
	if len(e.log) != 1 || !strings.Contains(e.log[0], "check-key") {
		t.Fatalf("calls after key failure: %q", e.log)
	}
	if got := ls(t, e.st); strings.Join(got, ",") != strings.Join(before, ",") {
		t.Fatalf("state dir changed: %v -> %v", before, got)
	}
	if read(t, filepath.Join(e.st, "secrets.env")) != "GLOW_SECRET_KEY=live\n" {
		t.Fatal("secrets changed")
	}
}

func TestKeyNeverInArgvAndGoesOnStdin(t *testing.T) {
	e := setup(t)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	for _, c := range e.log {
		if strings.Contains(c, key) {
			t.Fatalf("key in argv: %s", c)
		}
	}
	n := 0
	for c, in := range e.docker.stdins {
		if strings.Contains(c, "check-key") || strings.Contains(c, "restore-files") {
			if in != key+"\n" {
				t.Fatalf("%s stdin %q", c, in)
			}
			n++
		}
	}
	if n != 2 {
		t.Fatalf("key passed on stdin to %d restic calls, want 2", n)
	}
	if strings.Contains(e.out.String(), key) || strings.Contains(e.out.String(), "restoredpg") {
		t.Fatal("secret printed")
	}
}

func TestRefusesLiveData(t *testing.T) {
	for name, prep := range map[string]func(*env){
		"running containers": func(e *env) { e.docker.running = "c0ffee\n" },
		"glow db dir":        func(e *env) { os.MkdirAll(filepath.Join(e.st, "glow-postgres", "base"), 0o700) },
		"odk db dir":         func(e *env) { os.MkdirAll(filepath.Join(e.st, "odk-postgres", "base"), 0o700) },
	} {
		t.Run(name, func(t *testing.T) {
			e := setup(t)
			prep(e)
			err := Run(context.Background(), e.o)
			if err == nil || !strings.Contains(err.Error(), "--force") {
				t.Fatalf("want refusal mentioning --force, got %v", err)
			}
			for _, c := range e.log {
				if strings.HasPrefix(c, "compose") || strings.Contains(c, "restore-files") {
					t.Fatalf("touched the stack: %s", c)
				}
			}
			if read(t, filepath.Join(e.st, "secrets.env")) != "GLOW_SECRET_KEY=live\n" {
				t.Fatal("secrets changed")
			}
		})
	}
}

func TestForceReplacesLiveDataKeepingOriginals(t *testing.T) {
	e := setup(t)
	e.o.Force = true
	e.docker.running = "c0ffee\n"
	os.MkdirAll(filepath.Join(e.st, "glow-postgres", "base"), 0o700)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(e.out.String(), "will be replaced") {
		t.Fatalf("no warning: %s", e.out.String())
	}
	if _, err := os.Stat(filepath.Join(e.st, "glow-postgres.pre-restore", "base")); err != nil {
		t.Fatal("old GLOW DB dir not kept:", err)
	}
	if got := ls(t, filepath.Join(e.st, "glow-postgres")); len(got) != 0 {
		t.Fatalf("glow-postgres not fresh: %v", got)
	}
	if _, err := os.Stat(filepath.Join(e.st, "odk-postgres.pre-restore")); err == nil {
		t.Fatal("empty odk-postgres should not be moved aside")
	}
}

func TestCommandOrderExact(t *testing.T) {
	e := setup(t)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	img := "ghcr.io/oxwrc/glow-backup:v1.2.3"
	var staging string
	for _, c := range e.log {
		if i := strings.Index(c, ":/staging"); i > 0 {
			f := strings.Fields(c[:i])
			staging = f[len(f)-1]
		}
	}
	if filepath.Dir(staging) != e.st || !strings.HasPrefix(filepath.Base(staging), ".restore-staging-") {
		t.Fatalf("staging %q not a temp dir in the state dir", staging)
	}
	want := []string{
		"docker run --rm -i --network none -v " + e.from + ":/repo:ro -e RESTIC_REPOSITORY=/repo " + img + " glow-backup.sh check-key latest",
		"docker ps -a --filter label=com.docker.compose.project=glow --format {{.ID}}",
		"docker ps -q --filter label=com.docker.compose.project=glow",
		"compose down",
		"docker run --rm -i --network none -v " + e.from + ":/repo:ro -v " + staging + ":/staging -e RESTIC_REPOSITORY=/repo " + img + " glow-backup.sh restore-files abc123full /staging",
		"compose up -d --wait --pull missing api-db postgres14",
		"compose run --rm --no-deps -T -v " + staging + "/tmp/glow-dumps:/restore:ro backup glow-backup.sh restore-dbs /restore",
		"compose up -d --pull missing",
	}
	if strings.Join(e.log, "\n") != strings.Join(want, "\n") {
		t.Fatalf("calls:\n%s\nwant:\n%s", strings.Join(e.log, "\n"), strings.Join(want, "\n"))
	}
	if e.gets < 3 {
		t.Fatalf("health checks: %d", e.gets)
	}
	if _, err := os.Stat(staging); !os.IsNotExist(err) {
		t.Fatal("staging (database dumps) not removed")
	}
	out := e.out.String()
	if !strings.Contains(out, "abc123full") || !strings.Contains(out, "2026-10-19T10:00:00Z") {
		t.Fatalf("snapshot id/time not printed: %s", out)
	}
}

func TestNoComposeDownBeforeInstall(t *testing.T) {
	e := setup(t)
	os.Remove(filepath.Join(e.st, "runtime.env"))
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	for _, c := range e.log {
		if c == "compose down" {
			t.Fatal("compose down without runtime.env")
		}
	}
	for _, d := range []string{"glow-postgres", "odk-postgres", "backup-status", "odk-transfer"} {
		if _, err := os.Stat(filepath.Join(e.st, d)); err != nil {
			t.Fatalf("state dir %s not created", d)
		}
	}
}

func TestFilesSwappedAndGlowEnvSideBySide(t *testing.T) {
	e := setup(t)
	// A previous restore's backup copy must not be overwritten.
	os.WriteFile(filepath.Join(e.st, "secrets.env.pre-restore"), []byte("older\n"), 0o600)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	if read(t, filepath.Join(e.rel, "glow.env")) != "GLOW_DOMAIN=example.org\n" {
		t.Fatal("live glow.env overwritten")
	}
	if read(t, filepath.Join(e.rel, "glow.env.restored")) != "GLOW_DOMAIN=old.example.org\n" {
		t.Fatal("glow.env.restored wrong")
	}
	if !strings.Contains(e.out.String(), "glow.env.restored") {
		t.Fatal("operator not told about glow.env.restored")
	}
	if read(t, filepath.Join(e.st, "secrets.env")) != restoredSecrets {
		t.Fatal("secrets.env not restored")
	}
	if read(t, filepath.Join(e.st, "secrets.env.pre-restore")) != "older\n" ||
		read(t, filepath.Join(e.st, "secrets.env.pre-restore.2")) != "GLOW_SECRET_KEY=live\n" {
		t.Fatal("original secrets.env not kept beside")
	}
	if _, err := os.Stat(filepath.Join(e.st, "vintages.pre-restore", "live")); err != nil {
		t.Fatal("original vintages not kept")
	}
	for p, want := range map[string]string{
		"odk-secrets/enketo-secret":        "restored-enketo",
		"vintages/20260101T000000Z/m.json": "restored-vintage",
		"odk-forms-state.json":             "{\"restored\":true}",
	} {
		if read(t, filepath.Join(e.st, p)) != want {
			t.Fatalf("%s not restored", p)
		}
	}
	rt := read(t, filepath.Join(e.st, "runtime.env"))
	if !strings.Contains(rt, "POSTGRES_PASSWORD=restoredpg\n") || !strings.Contains(rt, "ODK_POSTGRES_PASSWORD=restoredodk\n") {
		t.Fatalf("runtime.env not regenerated from restored secrets:\n%s", rt)
	}
	if d := read(t, filepath.Join(e.st, "deployment.json")); !strings.Contains(d, "v1.2.3") || !strings.Contains(d, `"release_dir":"`+e.rel+`"`) {
		t.Fatal("deployment.json not written for this release")
	}
}

func TestSnapshotWithoutSecretsFailsBeforeSwapping(t *testing.T) {
	e := setup(t)
	e.docker.noSnap = []string{"state/secrets.env"}
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "secrets.env") {
		t.Fatalf("want secrets.env error, got %v", err)
	}
	if read(t, filepath.Join(e.st, "secrets.env")) != "GLOW_SECRET_KEY=live\n" {
		t.Fatal("secrets changed")
	}
	if _, err := os.Stat(filepath.Join(e.st, "vintages", "live")); err != nil {
		t.Fatal("vintages swapped before the snapshot was checked")
	}
}

func TestEveryStepFailureStops(t *testing.T) {
	steps := []string{"check-key", "docker ps", "compose down", "restore-files",
		"compose up -d --wait", "compose run", "compose up -d --pull missing"}
	for i, s := range steps {
		t.Run(s, func(t *testing.T) {
			e := setup(t)
			if strings.HasPrefix(s, "compose") {
				e.comp.failOn = s
			} else {
				e.docker.failOn = s
			}
			err := Run(context.Background(), e.o)
			if err == nil {
				t.Fatal("want error")
			}
			last := e.log[len(e.log)-1]
			if !strings.Contains(last, s) {
				t.Fatalf("ran past failing %q: last call %q", s, last)
			}
			if i < len(steps)-1 && e.gets > 0 {
				t.Fatal("health checked after a failure")
			}
			for _, d := range ls(t, e.st) {
				if strings.HasPrefix(d, ".restore-staging-") {
					t.Fatal("staging (database dumps) left behind")
				}
			}
			postSwap := i >= 4
			if got := strings.Contains(err.Error(), ".pre-restore"); got != postSwap {
				t.Fatalf("post-swap guidance %v, want %v: %v", got, postSwap, err)
			}
			if postSwap {
				assertGuidance(t, e, err)
			}
		})
	}
}

// assertGuidance: a failure after the swap names the kept originals, the
// retry and the manual rollback.
func assertGuidance(t *testing.T, e *env, err error) {
	t.Helper()
	m := err.Error()
	for _, want := range []string{
		filepath.Join(e.st, "secrets.env.pre-restore"), filepath.Join(e.st, "vintages.pre-restore"),
		"glowctl restore --force", ".pre-restore.N", "glowctl install", "move each",
	} {
		if !strings.Contains(m, want) {
			t.Fatalf("guidance lacks %q:\n%s", want, m)
		}
	}
}

func TestSecretsFailureAfterSwapGuides(t *testing.T) {
	e := setup(t)
	e.docker.secrets = "GLOW_SECRET_KEY=only\n" // missing required keys
	err := Run(context.Background(), e.o)
	if err == nil {
		t.Fatal("want error")
	}
	assertGuidance(t, e, err)
	for _, c := range e.log {
		if strings.HasPrefix(c, "compose up") {
			t.Fatalf("started DBs after a secrets failure: %s", c)
		}
	}
}

func TestHealthFailure(t *testing.T) {
	e := setup(t)
	e.o.Get = func(context.Context, *config.Config, string, string) (int, error) { return 502, nil }
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "502") {
		t.Fatalf("want health error, got %v", err)
	}
	assertGuidance(t, e, err)
	if !strings.Contains(err.Error(), "left running") || !strings.Contains(err.Error(), "deployment.json was not written") {
		t.Fatalf("health failure lacks stack/deployment note: %v", err)
	}
	if b, _ := os.ReadFile(filepath.Join(e.st, "deployment.json")); strings.Contains(string(b), "v1.2.3") {
		t.Fatal("deployment.json written after a failed health check")
	}
}

func TestSnapshotFromNewerReleaseRefused(t *testing.T) {
	e := setup(t)
	e.docker.version = "v1.3.0"
	before := ls(t, e.st)
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "v1.3.0") || !strings.Contains(err.Error(), "v1.2.3") {
		t.Fatalf("want newer-snapshot refusal naming both versions, got %v", err)
	}
	if len(e.log) != 1 || strings.Join(ls(t, e.st), ",") != strings.Join(before, ",") {
		t.Fatalf("touched something: calls %q", e.log)
	}
}

func TestSnapshotVersionOlderOrDevAllowed(t *testing.T) {
	for v, warn := range map[string]bool{"v1.2.3": false, "v1.0.9": false, "dev": true} {
		e := setup(t)
		e.docker.version = v
		if err := Run(context.Background(), e.o); err != nil {
			t.Fatalf("%s: %v", v, err)
		}
		if got := strings.Contains(e.out.String(), "cannot compare"); got != warn {
			t.Fatalf("%s: version warning %v, want %v\n%s", v, got, warn, e.out.String())
		}
	}
}

func TestGlowEnvLineOnlyWhenWritten(t *testing.T) {
	e := setup(t)
	e.docker.noSnap = []string{"release/glow.env"}
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(e.out.String(), "glow.env.restored") {
		t.Fatalf("mentions glow.env.restored though none was written:\n%s", e.out.String())
	}
}

func TestBackupPathKeyMismatchWarns(t *testing.T) {
	e := setup(t)
	bp := t.TempDir()
	os.WriteFile(filepath.Join(bp, "config"), []byte("x"), 0o600)
	e.o.Cfg.BackupPath = bp
	e.docker.failOn = bp + ":/repo:ro"
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	last := e.log[len(e.log)-1]
	if !strings.Contains(last, bp+":/repo:ro") || !strings.Contains(last, "check-key --key-only") {
		t.Fatalf("backup path not checked last: %q", last)
	}
	if in := e.docker.stdins[last]; in != strings.Repeat("ab", 32)+"\n" {
		t.Fatalf("backup path checked with %q, want the restored GLOW_BACKUP_KEY", in)
	}
	if !strings.Contains(e.out.String(), "WARNING: backups will fail") {
		t.Fatalf("no backup-path warning:\n%s", e.out.String())
	}
}

func TestBackupPathSameOrEmptyNotChecked(t *testing.T) {
	for name, set := range map[string]func(*env){
		"same as --from": func(e *env) {
			os.WriteFile(filepath.Join(e.from, "config"), []byte("x"), 0o600)
			e.o.Cfg.BackupPath = e.from + "/"
		},
		"empty": func(e *env) { e.o.Cfg.BackupPath = t.TempDir() },
	} {
		t.Run(name, func(t *testing.T) {
			e := setup(t)
			set(e)
			if err := Run(context.Background(), e.o); err != nil {
				t.Fatal(err)
			}
			n := 0
			for _, c := range e.log {
				if strings.Contains(c, "check-key") {
					n++
				}
			}
			if n != 1 {
				t.Fatalf("check-key ran %d times", n)
			}
		})
	}
}

func TestDemoRefused(t *testing.T) {
	e := setup(t)
	e.o.Cfg.Demo = true
	if err := Run(context.Background(), e.o); err == nil || len(e.log) != 0 {
		t.Fatalf("demo: err %v calls %q", err, e.log)
	}
}

func TestInstanceProjectFilterAndHint(t *testing.T) {
	e := setup(t)
	e.o.Cfg.Instance = "demo"
	e.comp.failOn = "compose run" // after the swap: the partial() hint
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "docker compose -p glow-demo down") {
		t.Fatalf("hint lacks -p glow-demo: %v", err)
	}
	for _, w := range []string{"docker ps -a --filter label=com.docker.compose.project=glow-demo ",
		"docker ps -q --filter label=com.docker.compose.project=glow-demo"} {
		found := false
		for _, c := range e.log {
			found = found || strings.HasPrefix(c, w)
		}
		if !found {
			t.Fatalf("no %q in %q", w, e.log)
		}
	}
}

func TestInstanceMismatchRefusedBeforeAnything(t *testing.T) {
	e := setup(t)
	os.WriteFile(filepath.Join(e.st, ".glow-instance"), []byte("glow-other\n"), 0o644)
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "belongs to project glow-other") || len(e.log) != 0 {
		t.Fatalf("err %v calls %q", err, e.log)
	}
}

func TestFreshStateDirClaimedAndMarkerWritten(t *testing.T) {
	e := setup(t)
	e.o.Cfg.Instance = "demo"
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	if read(t, filepath.Join(e.st, ".glow-instance")) != "glow-demo\n" {
		t.Fatal(".glow-instance not written")
	}
	if _, err := os.Stat(filepath.Join(e.st, ".backup-key-shown")); err != nil {
		t.Fatal("backup key marker not written: a later install would print the key again")
	}
	if fi, err := os.Stat(filepath.Join(e.st, "certs")); err != nil || !fi.IsDir() {
		t.Fatal("certs dir not created (acme mode)")
	}
}

func TestForeignProjectRefused(t *testing.T) {
	e := setup(t)
	e.o.Force = true
	e.docker.running = "c1\nc2\n"
	e.docker.inspect = "/glow-dashboard-1 []\n/glow-proxy-1 [{\"Type\":\"bind\",\"Source\":\"/var/lib/other/caddy-data\"}]\n"
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "project glow belongs to another GLOW stack; set GLOW_INSTANCE") {
		t.Fatalf("err %v", err)
	}
	for _, c := range e.log {
		if strings.HasPrefix(c, "compose") || strings.Contains(c, "restore-files") {
			t.Fatalf("touched the other stack: %s", c)
		}
	}
	if _, err := os.Stat(filepath.Join(e.st, ".glow-instance")); err == nil {
		t.Fatal("state dir claimed for project glow")
	}
}

func TestOwnProjectAccepted(t *testing.T) {
	e := setup(t)
	e.o.Force = true
	e.docker.running = "c1\nc2\n"
	e.docker.inspect = "/glow-dashboard-1 []\n/glow-proxy-1 [{\"Type\":\"bind\",\"Source\":\"" + e.rel + "/proxy/Caddyfile\"},{\"Type\":\"bind\",\"Source\":\"" + e.st + "/caddy-data\"}]\n"
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
}

func TestBundleLoadedBeforeKeyCheck(t *testing.T) {
	e := setup(t)
	b := t.TempDir()
	files := map[string]string{"glow-images-v1.2.3.tar.gz.part-aa": "img", "digests.json": "{}"}
	sums := ""
	for n, c := range files {
		os.WriteFile(filepath.Join(b, n), []byte(c), 0o644)
		h := sha256.Sum256([]byte(c))
		sums += hex.EncodeToString(h[:]) + "  " + n + "\n"
	}
	os.WriteFile(filepath.Join(b, "SHA256SUMS"), []byte(sums), 0o644)
	e.o.Bundle = b
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	if len(e.log) < 2 || e.log[0] != "docker load" || !strings.Contains(e.log[1], "check-key") {
		t.Fatalf("calls %q", e.log)
	}
}

func TestRestorePointsCurrentAtRelease(t *testing.T) {
	e := setup(t)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatal(err)
	}
	cur := filepath.Join(filepath.Dir(e.rel), "current")
	if l, err := os.Readlink(cur); err != nil || l != filepath.Base(e.rel) {
		t.Fatalf("current -> %q, %v", l, err)
	}
	if d, err := state.ReadDeployment(e.st); err != nil || d.ReleaseDir != e.rel {
		t.Fatalf("release_dir %q, %v; want the real dir %s", d.ReleaseDir, err, e.rel)
	}
}
