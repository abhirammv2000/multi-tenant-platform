package main

import (
	"context"
	"sort"
	"time"

	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	discoverylisters "k8s.io/client-go/listers/discovery/v1"
	"k8s.io/client-go/tools/cache"
)

// tenantService is the Service every tenant's app is deployed behind.
const tenantService = "tenant-app"

// EndpointCache keeps the ready pod IPs of every tenant's tenant-app Service in
// memory, kept current by a watch on the Kubernetes API. The Node proxy this
// replaces asked the API server on every request. Here a request is a map
// lookup, and the API server is only contacted when something changes.
type EndpointCache struct {
	factory informers.SharedInformerFactory
	lister  discoverylisters.EndpointSliceLister
	synced  cache.InformerSynced
}

func NewEndpointCache(client kubernetes.Interface, resync time.Duration) *EndpointCache {
	factory := informers.NewSharedInformerFactoryWithOptions(client, resync,
		informers.WithTweakListOptions(func(o *metav1.ListOptions) {
			o.LabelSelector = discoveryv1.LabelServiceName + "=" + tenantService
		}),
	)
	slices := factory.Discovery().V1().EndpointSlices()
	return &EndpointCache{
		factory: factory,
		lister:  slices.Lister(),
		synced:  slices.Informer().HasSynced,
	}
}

// Start begins watching and returns once the first full list has loaded, or
// the context ends.
func (c *EndpointCache) Start(ctx context.Context) bool {
	c.factory.Start(ctx.Done())
	return cache.WaitForCacheSync(ctx.Done(), c.synced)
}

func (c *EndpointCache) Synced() bool { return c.synced() }

// Endpoints returns the IPs of pods that are ready to serve, sorted so the
// result doesn't change order between calls.
func (c *EndpointCache) Endpoints(namespace string) []string {
	slices, err := c.lister.EndpointSlices(namespace).List(labels.Everything())
	if err != nil {
		return nil
	}
	return readyAddresses(slices)
}

// readyAddresses collects the addresses of ready endpoints from a set of
// EndpointSlices. An endpoint with no ready condition set counts as ready, as
// the API documents.
func readyAddresses(slices []*discoveryv1.EndpointSlice) []string {
	var ips []string
	for _, s := range slices {
		for _, ep := range s.Endpoints {
			if ep.Conditions.Ready != nil && !*ep.Conditions.Ready {
				continue
			}
			ips = append(ips, ep.Addresses...)
		}
	}
	sort.Strings(ips)
	return ips
}
