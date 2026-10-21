package update

import "testing"

func TestCompare(t *testing.T) {
	for _, c := range []struct {
		a, b string
		want int
		ok   bool
	}{
		{"v1.2.3", "v1.2.3", 0, true},
		{"v1.2.4", "v1.2.3", 1, true},
		{"v1.2.3", "v1.2.4", -1, true},
		{"v1.10.0", "v1.9.9", 1, true},
		{"v2.0.0", "v1.99.99", 1, true},
		{"v0.9.0", "v1.0.0", -1, true},
		{"v1.0.10", "v1.0.9", 1, true},
		{"v1.2.3", "dev", 0, false},
		{"dev", "v1.2.3", 0, false},
		{"1.2.3", "v1.2.3", 0, false}, // tags carry the v
		{"v1.2", "v1.2.0", 0, false},
		{"v1.2.3-rc1", "v1.2.3", 0, false},
		{"v01.2.3", "v1.2.3", 0, false},
		{"", "v1.2.3", 0, false},
	} {
		got, ok := Compare(c.a, c.b)
		if got != c.want || ok != c.ok {
			t.Errorf("Compare(%q, %q) = %d, %v; want %d, %v", c.a, c.b, got, ok, c.want, c.ok)
		}
	}
}
