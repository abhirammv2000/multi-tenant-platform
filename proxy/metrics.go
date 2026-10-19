package main

import (
	"github.com/prometheus/client_golang/prometheus"
)

// noTenant is the tenant label for requests that didn't reach a real tenant.
// The label otherwise holds only the id of a tenant that has running pods.
// Anyone can request /t/<any number>/, and using that number as a label would
// let them create unlimited time series.
const noTenant = "none"

// Cookie events: how a request got its backend.
const (
	cookieStickyHit     = "sticky_hit"     // valid cookie, and its pod is still running
	cookieNewAssignment = "new_assignment" // no cookie, so a pod was picked
	cookieRejected      = "rejected"       // forged, or its pod is gone, so a new pod was picked
)

type Metrics struct {
	Registry       *prometheus.Registry
	Requests       *prometheus.CounterVec
	Duration       *prometheus.HistogramVec
	UpstreamErrors prometheus.Counter
	CookieEvents   *prometheus.CounterVec
	CacheSynced    prometheus.Gauge
}

func NewMetrics() *Metrics {
	m := &Metrics{
		Registry: prometheus.NewRegistry(),
		Requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "proxy_requests_total",
			Help: "Requests handled, by tenant and response status.",
		}, []string{"tenant", "status"}),
		Duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "proxy_request_duration_seconds",
			Help:    "Time to serve a request, including the tenant app's response.",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5},
		}, []string{"tenant"}),
		UpstreamErrors: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "proxy_upstream_errors_total",
			Help: "Requests where the chosen pod could not be reached.",
		}),
		CookieEvents: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "proxy_cookie_events_total",
			Help: "How each request's backend was chosen: sticky hit, new assignment, or rejected cookie.",
		}, []string{"event"}),
		CacheSynced: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "proxy_endpoint_cache_synced",
			Help: "1 once the proxy has loaded the tenant endpoints from the Kubernetes API.",
		}),
	}
	m.Registry.MustRegister(m.Requests, m.Duration, m.UpstreamErrors, m.CookieEvents, m.CacheSynced)
	return m
}
