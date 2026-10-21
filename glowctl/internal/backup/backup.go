// Package backup triggers the backup service's on-demand runs.
package backup

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/compose"
)

func run(ctx context.Context, r compose.Runner, out io.Writer, action string) (string, error) {
	stdout, err := r.Run(ctx, []string{"exec", "-T", "backup", "glow-backup.sh", action}, nil)
	fmt.Fprint(out, stdout)
	return stdout, err
}

// Now takes a backup (then prunes), recording the outcome in the status file.
func Now(ctx context.Context, r compose.Runner, out io.Writer) error {
	_, err := run(ctx, r, out, "backup")
	return err
}

// NowSnapshot is Now, returning the ID of the snapshot it took (from the
// script's "snapshot_id=<id>" line).
func NowSnapshot(ctx context.Context, r compose.Runner, out io.Writer) (string, error) {
	stdout, err := run(ctx, r, out, "backup")
	if err != nil {
		return "", err
	}
	id := ""
	for _, l := range strings.Split(stdout, "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(l), "snapshot_id="); ok && v != "" {
			id = v
		}
	}
	if id == "" {
		return "", errors.New("the backup did not report its snapshot id (backup image too old?)")
	}
	return id, nil
}

// Verify runs a restore test of the latest snapshot.
func Verify(ctx context.Context, r compose.Runner, out io.Writer) error {
	_, err := run(ctx, r, out, "verify")
	return err
}
