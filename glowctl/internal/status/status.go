// Package status reports the health of a running GLOW stack.
package status

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/proxy"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

// Check is one line of the status report. Warn marks a passing check that
// still needs attention (it does not fail status).
type Check struct {
	Name   string
	OK     bool
	Warn   bool
	Detail string
}

// Options configures Run; Get defaults to proxy.Get, Now to time.Now.
type Options struct {
	Cfg     *config.Config
	Version string
	Runner  compose.Runner
	Get     func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
	Now     func() time.Time
}

// MaxBackupAge is how old the last successful backup may be; a fresh install
// gets the same grace period before its first scheduled backup is due.
const MaxBackupAge = 2 * time.Hour

// BackupStatus is <state>/backup-status/backup-status.json, written by the
// backup service. Timestamps are UTC "Z" strings, empty when never run.
type BackupStatus struct {
	LastBackupAt    string `json:"last_backup_at"`
	LastBackupOK    bool   `json:"last_backup_ok"`
	LastBackupError string `json:"last_backup_error"`
	LastVerifyAt    string `json:"last_verify_at"`
	LastVerifyOK    bool   `json:"last_verify_ok"`
	LastVerifyError string `json:"last_verify_error"`
	LastPruneAt     string `json:"last_prune_at"`
	LastPruneOK     bool   `json:"last_prune_ok"`
	LastPruneError  string `json:"last_prune_error"`
}

const logsHint = "; see `docker compose logs backup`"

// backupChecks reports backup age/outcome, the restore test and pruning.
func checkBackups(stateDir string, now time.Time) []Check {
	inGrace := false
	if d, err := state.ReadDeployment(stateDir); err == nil && !d.DeployedAt.IsZero() {
		inGrace = now.Sub(d.DeployedAt) <= MaxBackupAge
	}
	bc := Check{Name: "backup"}
	p := filepath.Join(stateDir, "backup-status", "backup-status.json")
	var bs BackupStatus
	b, err := os.ReadFile(p)
	if err == nil {
		err = json.Unmarshal(b, &bs)
	}
	if err != nil && !os.IsNotExist(err) {
		bc.Detail = fmt.Sprintf("cannot read %s: %v", p, err)
		return []Check{bc}
	}
	at, perr := time.Parse(time.RFC3339, bs.LastBackupAt)
	switch {
	case bs.LastBackupAt == "" && inGrace:
		bc.OK, bc.Detail = true, "no backup yet (first one is due within 2h of install)"
	case bs.LastBackupAt == "":
		bc.Detail = "no backup recorded" + logsHint
	case perr != nil:
		bc.Detail = fmt.Sprintf("unreadable last_backup_at %q", bs.LastBackupAt)
	case !bs.LastBackupOK:
		bc.Detail = "last backup failed at " + bs.LastBackupAt + ": " + bs.LastBackupError + logsHint
	case now.Sub(at) > MaxBackupAge && !inGrace:
		bc.Detail = "last successful backup " + bs.LastBackupAt + " is older than 2h" + logsHint
	default:
		bc.OK, bc.Detail = true, "last successful backup "+bs.LastBackupAt
	}
	cs := []Check{bc}

	vc := Check{Name: "restore test", OK: true, Detail: "not run yet"}
	if bs.LastVerifyAt != "" {
		if bs.LastVerifyOK {
			vc.Detail = "passed " + bs.LastVerifyAt
		} else {
			vc.OK, vc.Detail = false, "failed at "+bs.LastVerifyAt+": "+bs.LastVerifyError+logsHint
		}
	}
	cs = append(cs, vc)

	if bs.LastPruneAt != "" {
		pc := Check{Name: "prune", OK: true, Detail: "ok " + bs.LastPruneAt}
		if !bs.LastPruneOK {
			pc.Warn, pc.Detail = true, "failed at "+bs.LastPruneAt+": "+bs.LastPruneError+" (backups continue; the repository grows until pruning succeeds)"
		}
		cs = append(cs, pc)
	}
	return cs
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

// Run gathers the version, per-service, proxy and (production) backup checks.
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
		get = proxy.Get
	}
	scheme := "https://"
	if o.Cfg.TLSMode == "off" {
		scheme = "http://"
	}
	for _, t := range proxy.Targets(o.Cfg) {
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
	if !o.Cfg.Demo { // demo has no backup service
		now := time.Now
		if o.Now != nil {
			now = o.Now
		}
		cs = append(cs, checkBackups(o.Cfg.StateDir, now())...)
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
