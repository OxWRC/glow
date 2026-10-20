package status

import (
	"context"
	"errors"
	"io"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

type fake struct {
	out string
	err error
}

func (f fake) Run(context.Context, []string, io.Reader) (string, error) { return f.out, f.err }

const nd = `{"Service":"api","State":"running","Health":"healthy"}
{"Service":"proxy","State":"running","Health":""}
{"Service":"init","State":"exited","ExitCode":0,"Health":""}
`

func get(code int, err error) func(context.Context, *config.Config, string, string) (int, error) {
	return func(context.Context, *config.Config, string, string) (int, error) { return code, err }
}

func find(cs []Check, name string) *Check {
	for i := range cs {
		if cs[i].Name == name {
			return &cs[i]
		}
	}
	return nil
}

func TestRunHealthy(t *testing.T) {
	cfg := &config.Config{Domain: "d.org"}
	cs, err := Run(context.Background(), Options{Cfg: cfg, Version: "v1", Runner: fake{out: nd}, Get: get(200, nil)})
	if err != nil {
		t.Fatal(err)
	}
	if !AllOK(cs) {
		t.Fatalf("%+v", cs)
	}
	if c := find(cs, "version"); c == nil || c.Detail != "v1" {
		t.Fatalf("version: %+v", cs)
	}
}

func TestRunArrayFormatAndUnhealthy(t *testing.T) {
	arr := `[{"Service":"api","State":"running","Health":"unhealthy"},{"Service":"db","State":"exited","ExitCode":1}]`
	cs, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org"}, Runner: fake{out: arr}, Get: get(200, nil)})
	if err != nil {
		t.Fatal(err)
	}
	if AllOK(cs) || find(cs, "api").OK || find(cs, "db").OK {
		t.Fatalf("%+v", cs)
	}
}

func TestRunProxyDownAndComposeError(t *testing.T) {
	cs, _ := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org"}, Runner: fake{out: nd}, Get: get(502, nil)})
	if AllOK(cs) {
		t.Fatalf("502 should fail: %+v", cs)
	}
	cs, _ = Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org"}, Runner: fake{out: nd}, Get: get(0, errors.New("refused"))})
	if AllOK(cs) {
		t.Fatal("dial error should fail")
	}
	if _, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org"}, Runner: fake{err: errors.New("boom")}}); err == nil {
		t.Fatal("want compose error")
	}
	if cs, _ := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org"}, Runner: fake{out: ""}, Get: get(200, nil)}); AllOK(cs) {
		t.Fatal("no services must not be OK")
	}
}
