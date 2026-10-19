package main

import (
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
)

// fakeResolver maps a namespace to pod IPs.
type fakeResolver map[string][]string

func (f fakeResolver) Endpoints(ns string) []string { return f[ns] }

// upstream starts a tenant app on ip:port that reports which pod answered and
// what path and query it was asked for.
func upstream(t *testing.T, ip string, port string) (actualPort string) {
	t.Helper()
	l, err := net.Listen("tcp", net.JoinHostPort(ip, port))
	if err != nil {
		t.Skipf("cannot listen on %s: %v", ip, err)
	}
	srv := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "pod=%s path=%s query=%s xff=%s", ip, r.URL.Path, r.URL.RawQuery, r.Header.Get("X-Forwarded-For"))
	}))
	srv.Listener.Close()
	srv.Listener = l
	srv.Start()
	t.Cleanup(srv.Close)
	_, p, _ := net.SplitHostPort(l.Addr().String())
	return p
}

// twoPods starts two pods that share a port, on different loopback addresses.
func twoPods(t *testing.T) (port string) {
	t.Helper()
	port = upstream(t, "127.0.0.1", "0")
	upstream(t, "127.0.0.2", port)
	return port
}

func newHandler(res Resolver, port string, pick int) (*Handler, *Metrics) {
	m := NewMetrics()
	return &Handler{
		Resolver:     res,
		Signer:       NewSigner("test-secret"),
		Metrics:      m,
		UpstreamPort: port,
		Transport:    http.DefaultTransport,
		Log:          slog.New(slog.NewTextHandler(io.Discard, nil)),
		Pick:         func(n int) int { return pick % n },
	}, m
}

func do(h http.Handler, path string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
	req := httptest.NewRequest("GET", path, nil)
	req.RemoteAddr = "203.0.113.7:5555"
	for _, c := range cookies {
		req.AddCookie(c)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func cookieOf(rec *httptest.ResponseRecorder, name string) *http.Cookie {
	for _, c := range rec.Result().Cookies() {
		if c.Name == name {
			return c
		}
	}
	return nil
}

func TestPathsOutsideATenantAre404(t *testing.T) {
	h, m := newHandler(fakeResolver{}, "1", 0)
	for _, p := range []string{"/", "/foo", "/t/", "/t/abc/", "/t", "/tenant/3/"} {
		rec := do(h, p)
		if rec.Code != 404 {
			t.Errorf("%s: status %d, want 404", p, rec.Code)
		}
	}
	if got := testutil.ToFloat64(m.Requests.WithLabelValues("none", "404")); got != 6 {
		t.Errorf("404 count = %v, want 6", got)
	}
}

func TestATenantWithNoRunningPodsGets503(t *testing.T) {
	h, _ := newHandler(fakeResolver{}, "1", 0)
	rec := do(h, "/t/9/")
	if rec.Code != 503 || !strings.Contains(rec.Body.String(), "no running deployment for tenant 9") {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
}

func TestARequestIsRoutedAndGivenASignedCookie(t *testing.T) {
	port := twoPods(t)
	h, _ := newHandler(fakeResolver{"tenant-3": {"127.0.0.1", "127.0.0.2"}}, port, 1)

	rec := do(h, "/t/3/hello")

	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "pod=127.0.0.2") {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("X-Routed-Tenant") != "3" || rec.Header().Get("X-Routed-To") != "127.0.0.2" {
		t.Errorf("routing headers: %v", rec.Header())
	}
	c := cookieOf(rec, "affinity_t3")
	if c == nil {
		t.Fatal("no affinity cookie was set")
	}
	if ip, ok := h.Signer.Verify(c.Value); !ok || ip != "127.0.0.2" {
		t.Errorf("cookie %q verifies to %q, %v", c.Value, ip, ok)
	}
	if c.Path != "/t/3" || !c.HttpOnly {
		t.Errorf("cookie scope: path=%q httponly=%v", c.Path, c.HttpOnly)
	}
}

func TestAClientStaysOnItsPod(t *testing.T) {
	port := twoPods(t)
	res := fakeResolver{"tenant-3": {"127.0.0.1", "127.0.0.2"}}
	h, m := newHandler(res, port, 0)

	first := do(h, "/t/3/")
	cookie := cookieOf(first, "affinity_t3")
	if !strings.Contains(first.Body.String(), "pod=127.0.0.1") {
		t.Fatalf("first request went to %q", first.Body.String())
	}

	// Make a fresh pick land on the other pod. The cookie must win.
	h.Pick = func(n int) int { return 1 }
	for i := 0; i < 5; i++ {
		rec := do(h, "/t/3/", cookie)
		if !strings.Contains(rec.Body.String(), "pod=127.0.0.1") {
			t.Fatalf("request %d left its pod: %q", i, rec.Body.String())
		}
		if cookieOf(rec, "affinity_t3") != nil {
			t.Errorf("request %d was given a new cookie although it was sticky", i)
		}
	}
	if got := testutil.ToFloat64(m.CookieEvents.WithLabelValues(cookieStickyHit)); got != 5 {
		t.Errorf("sticky hits = %v, want 5", got)
	}
}

func TestAForgedCookieIsIgnoredAndReplaced(t *testing.T) {
	port := twoPods(t)
	h, m := newHandler(fakeResolver{"tenant-3": {"127.0.0.1", "127.0.0.2"}}, port, 0)
	forged := &http.Cookie{Name: "affinity_t3", Value: "127.0.0.2.deadbeef"}

	rec := do(h, "/t/3/", forged)

	if !strings.Contains(rec.Body.String(), "pod=127.0.0.1") {
		t.Errorf("a forged cookie steered the request: %q", rec.Body.String())
	}
	if cookieOf(rec, "affinity_t3") == nil {
		t.Error("no replacement cookie was issued")
	}
	if got := testutil.ToFloat64(m.CookieEvents.WithLabelValues(cookieRejected)); got != 1 {
		t.Errorf("rejected cookies = %v, want 1", got)
	}
}

func TestACookieForAPodThatIsGoneMovesTheClient(t *testing.T) {
	port := twoPods(t)
	h, _ := newHandler(fakeResolver{"tenant-3": {"127.0.0.1", "127.0.0.2"}}, port, 1)
	stale := &http.Cookie{Name: "affinity_t3", Value: h.Signer.Sign("10.9.9.9")} // valid, but not running

	rec := do(h, "/t/3/", stale)

	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "pod=127.0.0.2") {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
	c := cookieOf(rec, "affinity_t3")
	if c == nil {
		t.Fatal("the client kept a cookie for a pod that no longer exists")
	}
	if ip, _ := h.Signer.Verify(c.Value); ip != "127.0.0.2" {
		t.Errorf("new cookie points at %q", ip)
	}
}

func TestATenantsCookieDoesNothingForAnotherTenant(t *testing.T) {
	port := twoPods(t)
	res := fakeResolver{"tenant-3": {"127.0.0.1"}, "tenant-4": {"127.0.0.1", "127.0.0.2"}}
	h, _ := newHandler(res, port, 1)
	cookie3 := &http.Cookie{Name: "affinity_t3", Value: h.Signer.Sign("127.0.0.1")}

	rec := do(h, "/t/4/", cookie3)

	if !strings.Contains(rec.Body.String(), "pod=127.0.0.2") {
		t.Errorf("tenant 3's cookie affected tenant 4: %q", rec.Body.String())
	}
}

func TestThePathAndQueryReachTheApp(t *testing.T) {
	port := upstream(t, "127.0.0.1", "0")
	h, _ := newHandler(fakeResolver{"tenant-3": {"127.0.0.1"}}, port, 0)
	cases := map[string]string{
		"/t/3":           "path=/ query=",
		"/t/3/":          "path=/ query=",
		"/t/3/api/items": "path=/api/items query=",
		"/t/3/a?b=1&c=2": "path=/a query=b=1&c=2",
	}
	for in, want := range cases {
		if body := do(h, in).Body.String(); !strings.Contains(body, want) {
			t.Errorf("%s: app saw %q, want it to contain %q", in, body, want)
		}
	}
}

func TestTheClientAddressIsPassedOn(t *testing.T) {
	port := upstream(t, "127.0.0.1", "0")
	h, _ := newHandler(fakeResolver{"tenant-3": {"127.0.0.1"}}, port, 0)

	if body := do(h, "/t/3/").Body.String(); !strings.Contains(body, "xff=203.0.113.7") {
		t.Errorf("app did not see the client address: %q", body)
	}
}

func TestAnUnreachablePodIsA502AndIsCounted(t *testing.T) {
	// Nothing listens on this port.
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	_, dead, _ := net.SplitHostPort(l.Addr().String())
	l.Close()
	h, m := newHandler(fakeResolver{"tenant-3": {"127.0.0.1"}}, dead, 0)

	rec := do(h, "/t/3/")

	if rec.Code != 502 || !strings.HasPrefix(rec.Body.String(), "upstream error") {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
	if got := testutil.ToFloat64(m.UpstreamErrors); got != 1 {
		t.Errorf("upstream errors = %v, want 1", got)
	}
	if got := testutil.ToFloat64(m.Requests.WithLabelValues("3", "502")); got != 1 {
		t.Errorf("502 for tenant 3 = %v, want 1", got)
	}
}

func TestRequestsForUnknownTenantsDoNotCreateNewMetricSeries(t *testing.T) {
	h, m := newHandler(fakeResolver{}, "1", 0)
	for i := 0; i < 200; i++ {
		do(h, fmt.Sprintf("/t/%d/", 1000+i))
	}
	// Every one of them is a 503 for the single tenant label "none".
	if got := testutil.CollectAndCount(m.Requests); got != 1 {
		t.Errorf("%d time series after 200 different tenant ids, want 1", got)
	}
	if got := testutil.ToFloat64(m.Requests.WithLabelValues("none", "503")); got != 200 {
		t.Errorf("503 count = %v, want 200", got)
	}
}
