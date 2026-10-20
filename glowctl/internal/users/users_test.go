package users

import (
	"context"
	"io"
	"reflect"
	"strings"
	"testing"
)

type rec struct{ got [][]string }

func (r *rec) Run(_ context.Context, args []string, _ io.Reader) (string, error) {
	r.got = append(r.got, args)
	return "out\n", nil
}

func TestArgv(t *testing.T) {
	pre := []string{"exec", "-T", "api", "glow-api", "users"}
	cases := map[string][]string{
		"add a@b.org --admin":               {"create", "a@b.org", "--admin"},
		"add a@b.org --school A --school B": {"create", "a@b.org", "--schools", "A,B"},
		"add --school A a@b.org":            {"create", "a@b.org", "--schools", "A"},
		"add a@b.org":                       {"create", "a@b.org"},
		"list":                              {"list"},
		"disable a@b.org":                   {"update", "a@b.org", "--inactive"},
		"grant a@b.org --school X --school Y --no-admin": {"update", "a@b.org", "--schools", "X,Y", "--no-admin"},
		"grant a@b.org --admin":                          {"update", "a@b.org", "--admin"},
	}
	for in, want := range cases {
		r := &rec{}
		if err := Run(context.Background(), r, strings.Fields(in), io.Discard); err != nil {
			t.Fatalf("%s: %v", in, err)
		}
		if len(r.got) != 1 || !reflect.DeepEqual(r.got[0], append(append([]string{}, pre...), want...)) {
			t.Errorf("%s: got %v", in, r.got)
		}
	}
}

func TestErrors(t *testing.T) {
	for _, in := range []string{"", "bogus", "add", "add notanemail", "add a@b", "disable", "grant a@b.org",
		"grant a@b.org --admin --no-admin", "list extra", "add -a@b.org", "add a@b.org --school -x", "add a@b.org --school A,B", "grant a@b.org --school -x", "add a@b.org --nope"} {
		r := &rec{}
		if err := Run(context.Background(), r, strings.Fields(in), io.Discard); err == nil {
			t.Errorf("%q: want error", in)
		}
		if len(r.got) != 0 {
			t.Errorf("%q: ran compose", in)
		}
	}
}
