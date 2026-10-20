package bundle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeDocker struct {
	calls   [][]string
	loaded  string
	missing map[string]bool // refs that `image inspect` cannot find
}

func (f *fakeDocker) Run(_ context.Context, args []string, stdin io.Reader) (string, error) {
	f.calls = append(f.calls, args)
	if args[0] == "load" {
		b, _ := io.ReadAll(stdin)
		f.loaded = string(b)
		return "Loaded image\n", nil
	}
	if args[0] == "image" && args[1] == "inspect" && f.missing[args[len(args)-1]] {
		return "", errors.New("No such image")
	}
	return "sha256:x\n", nil
}

const (
	apiTag    = "ghcr.io/oxwrc/glow-api:v1.0.0"
	apiDigest = "ghcr.io/oxwrc/glow-api@sha256:aaaa"
	pgTag     = "postgres:16-alpine"
	pgDigest  = "postgres@sha256:bbbb"
)

// mkBundle writes a bundle and returns its dir; files maps name -> content.
func mkBundle(t *testing.T, files map[string]string, sumsFor []string) string {
	t.Helper()
	dir := t.TempDir()
	var sums strings.Builder
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range sumsFor {
		h := sha256.Sum256([]byte(files[name]))
		sums.WriteString(hex.EncodeToString(h[:]) + "  " + name + "\n")
	}
	if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(sums.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	return dir
}

func goodFiles() map[string]string {
	return map[string]string{
		"glow-images-v1.0.0.tar.gz.part-00": "AAA",
		"glow-images-v1.0.0.tar.gz.part-01": "BBB",
		"digests.json":                      `{"` + apiTag + `":"` + apiDigest + `","` + pgTag + `":"` + pgDigest + `"}`,
	}
}

func allNames(m map[string]string) []string {
	var o []string
	for k := range m {
		o = append(o, k)
	}
	return o
}

func mkRelease(t *testing.T) (string, string) {
	t.Helper()
	rel := t.TempDir()
	compose := "services:\n  api:\n    image: " + apiDigest + "\n  db:\n    image: " + pgDigest + "\n"
	for _, f := range []string{"compose.yml", "compose.demo.yml"} {
		if err := os.WriteFile(filepath.Join(rel, f), []byte(compose), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return rel, compose
}

func TestLoadVerifiesLoadsAndRewritesMissingRefs(t *testing.T) {
	files := goodFiles()
	b := mkBundle(t, files, allNames(files))
	rel, orig := mkRelease(t)
	d := &fakeDocker{missing: map[string]bool{apiDigest: true}}
	var out strings.Builder
	if err := Load(context.Background(), d, b, rel, &out); err != nil {
		t.Fatal(err)
	}
	if d.loaded != "AAABBB" {
		t.Fatalf("docker load stdin = %q, want parts in order", d.loaded)
	}
	for _, f := range []string{"compose.yml", "compose.demo.yml"} {
		got, _ := os.ReadFile(filepath.Join(rel, f))
		if strings.Contains(string(got), apiDigest) || !strings.Contains(string(got), "image: "+apiTag) {
			t.Fatalf("%s: api ref not rewritten:\n%s", f, got)
		}
		if !strings.Contains(string(got), "image: "+pgDigest) {
			t.Fatalf("%s: present ref must stay pinned:\n%s", f, got)
		}
		o, err := os.ReadFile(filepath.Join(rel, f+".orig"))
		if err != nil || string(o) != orig {
			t.Fatalf("%s.orig: %v %q", f, err, o)
		}
	}
}

func TestLoadNoRewriteWhenAllPresent(t *testing.T) {
	files := goodFiles()
	b := mkBundle(t, files, allNames(files))
	rel, orig := mkRelease(t)
	if err := Load(context.Background(), &fakeDocker{}, b, rel, io.Discard); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(filepath.Join(rel, "compose.yml"))
	if string(got) != orig {
		t.Fatal("rewrote compose.yml although every image is present")
	}
	if _, err := os.Stat(filepath.Join(rel, "compose.yml.orig")); err == nil {
		t.Fatal(".orig written without a rewrite")
	}
}

func TestLoadChecksumFailuresAbortBeforeDocker(t *testing.T) {
	cases := map[string]func(dir string, files map[string]string){
		"mismatch": func(dir string, _ map[string]string) {
			os.WriteFile(filepath.Join(dir, "glow-images-v1.0.0.tar.gz.part-01"), []byte("EVIL"), 0o644)
		},
		"missing listed file": func(dir string, _ map[string]string) {
			os.Remove(filepath.Join(dir, "digests.json"))
		},
		"part not in SHA256SUMS": func(dir string, _ map[string]string) {
			os.WriteFile(filepath.Join(dir, "glow-images-v1.0.0.tar.gz.part-02"), []byte("CCC"), 0o644)
		},
		"empty SHA256SUMS": func(dir string, _ map[string]string) {
			os.WriteFile(filepath.Join(dir, "SHA256SUMS"), nil, 0o644)
		},
		"path escape": func(dir string, _ map[string]string) {
			f, _ := os.OpenFile(filepath.Join(dir, "SHA256SUMS"), os.O_APPEND|os.O_WRONLY, 0)
			f.WriteString(strings.Repeat("0", 64) + "  ../secrets.env\n")
			f.Close()
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			files := goodFiles()
			b := mkBundle(t, files, allNames(files))
			mutate(b, files)
			rel, _ := mkRelease(t)
			d := &fakeDocker{}
			if err := Load(context.Background(), d, b, rel, io.Discard); err == nil {
				t.Fatal("want error")
			}
			if len(d.calls) != 0 {
				t.Fatalf("docker called despite bad bundle: %v", d.calls)
			}
		})
	}
}

func TestLoadRequiresParts(t *testing.T) {
	files := map[string]string{"digests.json": "{}"}
	b := mkBundle(t, files, allNames(files))
	rel, _ := mkRelease(t)
	if err := Load(context.Background(), &fakeDocker{}, b, rel, io.Discard); err == nil {
		t.Fatal("want error for a bundle without image parts")
	}
}
