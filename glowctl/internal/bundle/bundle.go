// Package bundle loads an offline release bundle: verified image tarball
// parts plus digests.json (tag ref -> digest ref).
package bundle

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/state"
)

// Runner runs `docker <args...>` with optional stdin, returning stdout.
type Runner interface {
	Run(ctx context.Context, args []string, stdin io.Reader) (string, error)
}

// Docker runs the real docker CLI; stdin is streamed, not buffered.
type Docker struct{}

func (Docker) Run(ctx context.Context, args []string, stdin io.Reader) (string, error) {
	cmd := exec.CommandContext(ctx, "docker", args...)
	cmd.Stdin = stdin
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		return out.String(), fmt.Errorf("docker %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(errb.String()))
	}
	return out.String(), nil
}

// composeFiles are the release files whose image refs may be rewritten.
var composeFiles = []string{"compose.yml", "compose.demo.yml"}

// Load verifies SHA256SUMS, pipes the image parts into `docker load`, then
// rewrites any digest ref in the release compose files that the image store
// lacks (some stores drop digest-only names) to its tag ref, keeping a .orig.
func Load(ctx context.Context, d Runner, bundleDir, releaseDir string, out io.Writer) error {
	listed, err := verify(bundleDir)
	if err != nil {
		return err
	}
	parts, err := filepath.Glob(filepath.Join(bundleDir, "glow-images-*.tar.gz.part-*"))
	if err != nil {
		return err
	}
	if len(parts) == 0 {
		return fmt.Errorf("bundle %s: no glow-images-*.tar.gz.part-* files", bundleDir)
	}
	sort.Strings(parts)
	for _, p := range append(parts, filepath.Join(bundleDir, "digests.json")) {
		if !listed[filepath.Base(p)] {
			return fmt.Errorf("bundle %s: %s is not listed in SHA256SUMS", bundleDir, filepath.Base(p))
		}
	}
	raw, err := os.ReadFile(filepath.Join(bundleDir, "digests.json"))
	if err != nil {
		return err
	}
	var digests map[string]string
	if err := json.Unmarshal(raw, &digests); err != nil {
		return fmt.Errorf("bundle digests.json: %w", err)
	}

	fmt.Fprintf(out, "[glowctl] loading %d image archive part(s)\n", len(parts))
	var readers []io.Reader
	for _, p := range parts {
		f, err := os.Open(p)
		if err != nil {
			return err
		}
		defer f.Close()
		readers = append(readers, f)
	}
	if _, err := d.Run(ctx, []string{"load"}, io.MultiReader(readers...)); err != nil {
		return err
	}
	return rewrite(ctx, d, digests, releaseDir, out)
}

// verify checks every file named in SHA256SUMS and returns the listed names.
func verify(dir string) (map[string]bool, error) {
	f, err := os.Open(filepath.Join(dir, "SHA256SUMS"))
	if err != nil {
		return nil, fmt.Errorf("bundle: %w", err)
	}
	defer f.Close()
	listed := map[string]bool{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		sum, name, ok := strings.Cut(line, " ")
		name = strings.TrimPrefix(strings.TrimSpace(name), "*")
		if !ok || len(sum) != 64 || name == "" || name != filepath.Base(name) || name == ".." {
			return nil, fmt.Errorf("bundle SHA256SUMS: malformed line %q", line)
		}
		got, err := fileSHA256(filepath.Join(dir, name))
		if err != nil {
			return nil, fmt.Errorf("bundle: %s: %w", name, err)
		}
		if !strings.EqualFold(got, sum) {
			return nil, fmt.Errorf("bundle: %s: checksum mismatch (corrupt or tampered download)", name)
		}
		listed[name] = true
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	if len(listed) == 0 {
		return nil, fmt.Errorf("bundle SHA256SUMS lists no files")
	}
	return listed, nil
}

func fileSHA256(p string) (string, error) {
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func rewrite(ctx context.Context, d Runner, digests map[string]string, releaseDir string, out io.Writer) error {
	tags := make([]string, 0, len(digests))
	for t := range digests {
		tags = append(tags, t)
	}
	sort.Strings(tags)
	tagFor := map[string]string{} // digest ref -> first tag ref (sorted)
	for _, t := range tags {
		if _, ok := tagFor[digests[t]]; !ok {
			tagFor[digests[t]] = t
		}
	}
	contents := map[string]string{}
	for _, name := range composeFiles {
		b, err := os.ReadFile(filepath.Join(releaseDir, name))
		if os.IsNotExist(err) {
			continue
		} else if err != nil {
			return err
		}
		contents[name] = string(b)
	}
	refs := make([]string, 0, len(tagFor))
	for r := range tagFor {
		refs = append(refs, r)
	}
	sort.Strings(refs)
	updated := map[string]string{}
	for _, ref := range refs {
		re := refRe(ref)
		used := false
		for _, c := range contents {
			used = used || re.MatchString(c)
		}
		if !used {
			continue
		}
		if _, err := d.Run(ctx, []string{"image", "inspect", "--format", "{{.Id}}", ref}, nil); err == nil {
			continue
		}
		fmt.Fprintf(out, "[glowctl] %s not in image store by digest; using %s\n", ref, tagFor[ref])
		for name, c := range contents {
			if re.MatchString(c) {
				contents[name] = re.ReplaceAllString(c, "${1}"+strings.ReplaceAll(tagFor[ref], "$", "$$")+"${2}")
				updated[name] = contents[name]
			}
		}
	}
	for _, name := range composeFiles {
		c, ok := updated[name]
		if !ok {
			continue
		}
		p := filepath.Join(releaseDir, name)
		orig, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		fi, err := os.Stat(p)
		if err != nil {
			return err
		}
		// Keep the first .orig: it is the pristine release file.
		if _, err := os.Stat(p + ".orig"); os.IsNotExist(err) {
			if err := state.WriteFileAtomic(p+".orig", orig, fi.Mode().Perm()); err != nil {
				return err
			}
		} else if err != nil {
			return err
		}
		if err := state.WriteFileAtomic(p, []byte(c), fi.Mode().Perm()); err != nil {
			return err
		}
	}
	return nil
}

// refRe matches ref as a whole token: bounded by start/whitespace/quote on
// each side, so postgres@sha256:X never matches inside library/postgres@sha256:X.
func refRe(ref string) *regexp.Regexp {
	return regexp.MustCompile(`(^|[\s"'])` + regexp.QuoteMeta(ref) + `($|[\s"'])`)
}
