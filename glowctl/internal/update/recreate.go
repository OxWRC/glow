package update

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// relMark stands for the release dir in normalised service configs.
const relMark = "@RELEASE@"

// service is one service of `compose config --format json`, with release
// dir paths replaced by relMark so two releases compare equal.
type service struct {
	Image string
	norm  string   // canonical JSON of the normalised config
	binds []string // bind sources under the release dir, relative to it
}

// parseServices parses `compose config --format json`; dirs are the paths
// the release dir may appear as (its real path, <parent>/current).
func parseServices(out string, dirs ...string) (map[string]service, error) {
	var c struct {
		Services map[string]any `json:"services"`
	}
	if err := json.Unmarshal([]byte(out), &c); err != nil {
		return nil, fmt.Errorf("parsing compose config: %w", err)
	}
	m := map[string]service{}
	for name, raw := range c.Services {
		v := normalise(raw, dirs)
		b, err := json.Marshal(v) // map keys sorted: canonical
		if err != nil {
			return nil, err
		}
		s := service{norm: string(b)}
		if obj, ok := v.(map[string]any); ok {
			s.Image, _ = obj["image"].(string)
			vols, _ := obj["volumes"].([]any)
			for _, vol := range vols {
				vm, _ := vol.(map[string]any)
				src, _ := vm["source"].(string)
				if vm["type"] == "bind" && strings.HasPrefix(src, relMark+"/") {
					s.binds = append(s.binds, strings.TrimPrefix(src, relMark+"/"))
				}
			}
		}
		m[name] = s
	}
	return m, nil
}

func normalise(v any, dirs []string) any {
	switch t := v.(type) {
	case string:
		for _, d := range dirs {
			if t == d {
				return relMark
			}
			if strings.HasPrefix(t, d+"/") {
				return relMark + t[len(d):]
			}
		}
		return t
	case []any:
		for i := range t {
			t[i] = normalise(t[i], dirs)
		}
		return t
	case map[string]any:
		for k := range t {
			t[k] = normalise(t[k], dirs)
		}
		return t
	}
	return v
}

// recreateSet lists the services of the new release that `up` must recreate:
// new services, those whose config differs (image, environment, ...) and
// those whose bind-mounted release files differ in content between oldDir
// and newDir. Compose itself only sees config changes, so the last group
// needs --force-recreate. hadCurrent false (the running stack predates
// <parent>/current) adds every service with release-dir binds: their mount
// paths move to the stable path once.
func recreateSet(old, cur map[string]service, oldDir, newDir string, hadCurrent bool) ([]string, error) {
	var out []string
	for name, n := range cur {
		o, ok := old[name]
		changed := !ok || o.norm != n.norm || (!hadCurrent && len(n.binds) > 0)
		for _, rel := range n.binds {
			if changed {
				break
			}
			same, err := sameTree(filepath.Join(oldDir, rel), filepath.Join(newDir, rel))
			if err != nil {
				return nil, err
			}
			changed = !same
		}
		if changed {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out, nil
}

// sameTree reports whether a and b (files, dirs or symlinks; both missing
// counts as the same) have the same kind, permissions and content.
func sameTree(a, b string) (bool, error) {
	fa, ea := os.Lstat(a)
	fb, eb := os.Lstat(b)
	if os.IsNotExist(ea) || os.IsNotExist(eb) {
		return os.IsNotExist(ea) && os.IsNotExist(eb), nil
	}
	if ea != nil {
		return false, ea
	}
	if eb != nil {
		return false, eb
	}
	if fa.Mode() != fb.Mode() {
		return false, nil
	}
	switch {
	case fa.Mode()&os.ModeSymlink != 0:
		la, err := os.Readlink(a)
		if err != nil {
			return false, err
		}
		lb, err := os.Readlink(b)
		return la == lb, err
	case fa.IsDir():
		da, err := os.ReadDir(a)
		if err != nil {
			return false, err
		}
		db, err := os.ReadDir(b)
		if err != nil {
			return false, err
		}
		if len(da) != len(db) {
			return false, nil
		}
		for i := range da {
			if da[i].Name() != db[i].Name() {
				return false, nil
			}
			if same, err := sameTree(filepath.Join(a, da[i].Name()), filepath.Join(b, db[i].Name())); !same || err != nil {
				return false, err
			}
		}
		return true, nil
	case fa.Size() != fb.Size():
		return false, nil
	}
	ba, err := os.ReadFile(a)
	if err != nil {
		return false, err
	}
	bb, err := os.ReadFile(b)
	return bytes.Equal(ba, bb), err
}
