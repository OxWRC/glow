// Package status reports the health of a running GLOW stack.
package status

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/install"
)

// Check is one line of the status report. Further checks (e.g. backups) are
// appended to the slice returned by Run.
type Check struct {
	Name   string
	OK     bool
	Detail string
}

// Options configures Run; Get defaults to install.ProxyGet.
type Options struct {
	Cfg     *config.Config
	Version string
	Runner  compose.Runner
	Get     func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
}

type psEntry struct {
	Service  string
	State    string
	Health   string
	ExitCode int
}

// parsePS accepts both newline-delimited JSON and a single JSON array.
func parsePS(out string) ([]psEntry, error) {
	out = strings.TrimSpace(out)
	if out == "" {
		return nil, nil
	}
	if strings.HasPrefix(out, "[") {
		var es []psEntry
		return es, json.Unmarshal([]byte(out), &es)
	}
	var es []psEntry
	dec := json.NewDecoder(strings.NewReader(out))
	for {
		var e psEntry
		if err := dec.Decode(&e); err == io.EOF {
			return es, nil
		} else if err != nil {
			return nil, err
		}
		es = append(es, e)
	}
}

// Run gathers the version, per-service and proxy checks.
func Run(ctx context.Context, o Options) ([]Check, error) {
	cs := []Check{{Name: "version", OK: true, Detail: o.Version}}
	out, err := o.Runner.Run(ctx, []string{"ps", "--all", "--format", "json"}, nil)
	if err != nil {
		return nil, err
	}
	es, err := parsePS(out)
	if err != nil {
		return nil, fmt.Errorf("parsing compose ps output: %w", err)
	}
	sort.Slice(es, func(i, j int) bool { return es[i].Service < es[j].Service })
	for _, e := range es {
		c := Check{Name: e.Service, Detail: e.State}
		switch {
		case e.State == "running" && (e.Health == "" || e.Health == "healthy"):
			c.OK = true
			if e.Health != "" {
				c.Detail += " (" + e.Health + ")"
			}
		case e.State == "exited" && e.ExitCode == 0:
			c.OK, c.Detail = true, "completed"
		default:
			if e.Health != "" {
				c.Detail += " (" + e.Health + ")"
			}
			if e.State == "exited" {
				c.Detail += fmt.Sprintf(", exit %d", e.ExitCode)
			}
		}
		cs = append(cs, c)
	}
	if len(es) == 0 {
		cs = append(cs, Check{Name: "services", Detail: "no containers found; run glowctl install"})
	}
	get := o.Get
	if get == nil {
		get = install.ProxyGet
	}
	scheme := "https://"
	if o.Cfg.TLSMode == "off" {
		scheme = "http://"
	}
	for _, t := range install.Targets(o.Cfg) {
		c := Check{Name: scheme + t[0] + t[1]}
		code, err := get(ctx, o.Cfg, t[0], t[1])
		switch {
		case err != nil:
			c.Detail = err.Error()
		case code != http.StatusOK:
			c.Detail = fmt.Sprintf("HTTP %d", code)
		default:
			c.OK, c.Detail = true, "HTTP 200"
		}
		cs = append(cs, c)
	}
	return cs, nil
}

// AllOK reports whether every check passed.
func AllOK(cs []Check) bool {
	for _, c := range cs {
		if !c.OK {
			return false
		}
	}
	return true
}
