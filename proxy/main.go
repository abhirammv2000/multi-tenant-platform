// The platform's session-affinity reverse proxy. Requests to /t/<tenant id>/...
// go to one of that tenant's running pods, and a signed cookie keeps a client on
// the same pod. This replaces the Node.js proxy in k8s/proxy/sticky-proxy.js.
package main

import (
	"context"
	"errors"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	secret := os.Getenv("PROXY_COOKIE_SECRET")
	if secret == "" {
		log.Error("PROXY_COOKIE_SECRET must be set")
		os.Exit(1)
	}
	addr := envOr("LISTEN_ADDR", ":3000")
	upstreamPort := envOr("UPSTREAM_PORT", "8080")

	client, err := kubeClient()
	if err != nil {
		log.Error("could not create the Kubernetes client", "error", err.Error())
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	metrics := NewMetrics()
	endpoints := NewEndpointCache(client, 10*time.Minute)

	handler := &Handler{
		Resolver:     endpoints,
		Signer:       NewSigner(secret),
		Metrics:      metrics,
		UpstreamPort: upstreamPort,
		Log:          log,
		Pick:         rand.IntN,
		Transport: &http.Transport{
			DialContext:           (&net.Dialer{Timeout: 3 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
			MaxIdleConns:          512,
			MaxIdleConnsPerHost:   64,
			IdleConnTimeout:       90 * time.Second,
			ResponseHeaderTimeout: 30 * time.Second,
		},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, _ *http.Request) {
		if !endpoints.Synced() {
			http.Error(w, "endpoint cache not loaded yet", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ok"))
	})
	mux.Handle("/metrics", promhttp.HandlerFor(metrics.Registry, promhttp.HandlerOpts{}))
	mux.Handle("/", handler)

	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// Load the endpoint cache in the background so /healthz answers straight
	// away. /readyz stays failing until it has loaded, so no traffic arrives
	// before the proxy knows where tenants' pods are.
	go func() {
		if !endpoints.Start(ctx) {
			return
		}
		metrics.CacheSynced.Set(1)
		log.Info("endpoint cache loaded")
	}()

	go func() {
		log.Info("listening", "addr", addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("server failed", "error", err.Error())
			stop()
		}
	}()

	<-ctx.Done()
	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Error("shutdown did not finish cleanly", "error", err.Error())
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// kubeClient uses the pod's service account in a cluster, and a kubeconfig
// (KUBECONFIG or ~/.kube/config) when run on a developer machine.
func kubeClient() (kubernetes.Interface, error) {
	cfg, err := rest.InClusterConfig()
	if err != nil {
		cfg, err = clientcmd.NewNonInteractiveDeferredLoadingClientConfig(
			clientcmd.NewDefaultClientConfigLoadingRules(), &clientcmd.ConfigOverrides{},
		).ClientConfig()
		if err != nil {
			return nil, err
		}
	}
	// Watches are long lived, so QPS only matters for the initial list.
	cfg.QPS, cfg.Burst = 20, 40
	return kubernetes.NewForConfig(cfg)
}
