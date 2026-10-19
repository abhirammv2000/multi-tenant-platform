package main

import "testing"

func TestSignedValueRoundTrips(t *testing.T) {
	s := NewSigner("secret")
	got, ok := s.Verify(s.Sign("10.244.0.5"))
	if !ok || got != "10.244.0.5" {
		t.Fatalf("Verify(Sign(ip)) = %q, %v", got, ok)
	}
}

// The expected signature was computed with Python's hmac and hashlib, not with
// this code, so this checks the format is the same one the Node proxy used and
// that cookies issued by either proxy are accepted by the other.
func TestSignatureMatchesTheNodeProxysFormat(t *testing.T) {
	s := NewSigner("test-secret")
	want := "10.244.0.5.6890247d58381d736cb6687e26f4bfedb1adf727f47092d64b6747ee4496e455"
	if got := s.Sign("10.244.0.5"); got != want {
		t.Fatalf("Sign = %q, want %q", got, want)
	}
	if _, ok := s.Verify(want); !ok {
		t.Fatal("a cookie signed the Node way was rejected")
	}
}

func TestTamperedCookiesAreRejected(t *testing.T) {
	s := NewSigner("secret")
	good := s.Sign("10.244.0.5")
	cases := map[string]string{
		"changed value":       "10.244.0.6" + good[len("10.244.0.5"):],
		"changed signature":   good[:len(good)-1] + "0",
		"signature removed":   "10.244.0.5.",
		"no separator":        "10244005",
		"empty":               "",
		"only a signature":    "." + good[len("10.244.0.5")+1:],
		"another secret":      NewSigner("other").Sign("10.244.0.5"),
		"extra dotted suffix": good + ".x",
	}
	for name, cookie := range cases {
		if v, ok := s.Verify(cookie); ok {
			t.Errorf("%s: accepted, value %q", name, v)
		}
	}
}
