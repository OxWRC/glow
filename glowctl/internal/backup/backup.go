// Package backup triggers the backup service's on-demand runs.
package backup

import (
	"context"
	"fmt"
	"io"

	"github.com/oxwrc/glow/glowctl/internal/compose"
)

func run(ctx context.Context, r compose.Runner, out io.Writer, action string) error {
	stdout, err := r.Run(ctx, []string{"exec", "-T", "backup", "glow-backup.sh", action}, nil)
	fmt.Fprint(out, stdout)
	return err
}

// Now takes a backup (then prunes), recording the outcome in the status file.
func Now(ctx context.Context, r compose.Runner, out io.Writer) error {
	return run(ctx, r, out, "backup")
}

// Verify runs a restore test of the latest snapshot.
func Verify(ctx context.Context, r compose.Runner, out io.Writer) error {
	return run(ctx, r, out, "verify")
}
