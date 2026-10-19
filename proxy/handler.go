package main

import (
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"time"
)

// tenantPath matches /t/<tenant id> and anything after it.
var tenantPath = regexp.MustCompile(`^/t/(\d+)(/.*)?$`)

// Resolver says which pod IPs are ready for a namespace's tenant-app Service.
type Resolver interface {
	Endpoints(namespace string) []string
}

// Handler routes /t/<tenant id>/... to one of that tenant's pods and keeps a
// client on the same pod with a signed cookie.
type Handler struct {
	Resolver     Resolver
	Signer       Signer
	Metrics      *Metrics
	UpstreamPort string
	Transport    http.RoundTripper
	Log          *slog.Logger
	// Pick returns an index in [0, n). Set in tests to make routing predictable.
	Pick func(n int) int
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
	tenant := noTenant
	defer func() {
		h.Metrics.Requests.WithLabelValues(tenant, fmt.Sprint(sw.status)).Inc()
		h.Metrics.Duration.WithLabelValues(tenant).Observe(time.Since(start).Seconds())
	}()

	m := tenantPath.FindStringSubmatch(r.URL.Path)
	if m == nil {
		http.Error(sw, "expected path /t/<tenant_id>/...", http.StatusNotFound)
		return
	}
	tenantID := m[1]
	forwardPath := m[2]
	if forwardPath == "" {
		forwardPath = "/"
	}

	ips := h.Resolver.Endpoints("tenant-" + tenantID)
	if len(ips) == 0 {
		http.Error(sw, fmt.Sprintf("no running deployment for tenant %s", tenantID), http.StatusServiceUnavailable)
		return
	}
	tenant = tenantID

	cookieName := "affinity_t" + tenantID
	target, event := h.choose(r, cookieName, ips)
	h.Metrics.CookieEvents.WithLabelValues(event).Inc()

	proxy := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL = &url.URL{
				Scheme:   "http",
				Host:     net.JoinHostPort(target, h.UpstreamPort),
				Path:     forwardPath,
				RawQuery: r.URL.RawQuery,
			}
			pr.Out.Host = r.Host
			pr.SetXForwarded()
		},
		ModifyResponse: func(resp *http.Response) error {
			resp.Header.Set("X-Routed-Tenant", tenantID)
			resp.Header.Set("X-Routed-To", target)
			if event != cookieStickyHit {
				resp.Header.Add("Set-Cookie", (&http.Cookie{
					Name:     cookieName,
					Value:    h.Signer.Sign(target),
					Path:     "/t/" + tenantID,
					HttpOnly: true,
					SameSite: http.SameSiteLaxMode,
				}).String())
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, err error) {
			h.Metrics.UpstreamErrors.Inc()
			h.Log.Warn("upstream error", "tenant", tenantID, "pod", target, "error", err.Error())
			http.Error(w, "upstream error: "+err.Error(), http.StatusBadGateway)
		},
		Transport: h.Transport,
	}
	proxy.ServeHTTP(sw, r)
}

// choose keeps the client on the pod in its cookie when that pod is still
// running, and otherwise picks one.
func (h *Handler) choose(r *http.Request, cookieName string, ips []string) (target, event string) {
	event = cookieNewAssignment
	if c, err := r.Cookie(cookieName); err == nil {
		event = cookieRejected
		if ip, ok := h.Signer.Verify(c.Value); ok && contains(ips, ip) {
			return ip, cookieStickyHit
		}
	}
	return ips[h.Pick(len(ips))], event
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// statusWriter remembers the status code so it can be recorded as a metric.
type statusWriter struct {
	http.ResponseWriter
	status      int
	wroteHeader bool
}

func (w *statusWriter) WriteHeader(code int) {
	if !w.wroteHeader {
		w.status = code
		w.wroteHeader = true
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *statusWriter) Write(b []byte) (int, error) {
	w.wroteHeader = true
	return w.ResponseWriter.Write(b)
}

// Flush lets streaming responses through the wrapper.
func (w *statusWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}
