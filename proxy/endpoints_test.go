package main

import (
	"context"
	"reflect"
	"testing"
	"time"

	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func boolPtr(b bool) *bool { return &b }

func slice(ns, name, service string, endpoints ...discoveryv1.Endpoint) *discoveryv1.EndpointSlice {
	return &discoveryv1.EndpointSlice{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    map[string]string{discoveryv1.LabelServiceName: service},
		},
		AddressType: discoveryv1.AddressTypeIPv4,
		Endpoints:   endpoints,
	}
}

func ep(ip string, ready *bool) discoveryv1.Endpoint {
	return discoveryv1.Endpoint{Addresses: []string{ip}, Conditions: discoveryv1.EndpointConditions{Ready: ready}}
}

func TestOnlyReadyPodsAreReturned(t *testing.T) {
	s := slice("tenant-1", "a", "tenant-app",
		ep("10.0.0.3", boolPtr(true)),
		ep("10.0.0.2", boolPtr(false)), // starting up or failing its probe
		ep("10.0.0.1", nil),            // no condition set means ready
	)
	got := readyAddresses([]*discoveryv1.EndpointSlice{s})
	if want := []string{"10.0.0.1", "10.0.0.3"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v (sorted, ready only)", got, want)
	}
}

func TestPodsFromSeveralSlicesAreCombined(t *testing.T) {
	a := slice("tenant-1", "a", "tenant-app", ep("10.0.0.1", boolPtr(true)))
	b := slice("tenant-1", "b", "tenant-app", ep("10.0.0.2", boolPtr(true)))
	got := readyAddresses([]*discoveryv1.EndpointSlice{b, a})
	if want := []string{"10.0.0.1", "10.0.0.2"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func startCache(t *testing.T, objs ...*discoveryv1.EndpointSlice) (*EndpointCache, *fake.Clientset) {
	t.Helper()
	client := fake.NewClientset()
	for _, o := range objs {
		if _, err := client.DiscoveryV1().EndpointSlices(o.Namespace).Create(context.Background(), o, metav1.CreateOptions{}); err != nil {
			t.Fatal(err)
		}
	}
	c := NewEndpointCache(client, 0)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	if !c.Start(ctx) {
		t.Fatal("cache did not sync")
	}
	return c, client
}

// eventually waits for the cache to catch up with a change made through the API.
func eventually(t *testing.T, what string, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if check() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for: %s", what)
}

func TestTheCacheLoadsExistingPodsAndReportsSynced(t *testing.T) {
	c, _ := startCache(t,
		slice("tenant-1", "a", "tenant-app", ep("10.0.0.1", boolPtr(true))),
		slice("tenant-2", "a", "tenant-app", ep("10.0.0.9", boolPtr(true))),
	)

	if !c.Synced() {
		t.Error("Synced() is false after Start returned true")
	}
	if got := c.Endpoints("tenant-1"); !reflect.DeepEqual(got, []string{"10.0.0.1"}) {
		t.Errorf("tenant-1: %v", got)
	}
	if got := c.Endpoints("tenant-2"); !reflect.DeepEqual(got, []string{"10.0.0.9"}) {
		t.Errorf("tenant-2: %v", got)
	}
	if got := c.Endpoints("tenant-3"); len(got) != 0 {
		t.Errorf("a tenant with no slices has endpoints: %v", got)
	}
}

func TestOtherServicesAreNotCached(t *testing.T) {
	c, _ := startCache(t,
		slice("tenant-1", "db", "postgres", ep("10.0.0.7", boolPtr(true))),
		slice("tenant-1", "app", "tenant-app", ep("10.0.0.1", boolPtr(true))),
	)
	if got := c.Endpoints("tenant-1"); !reflect.DeepEqual(got, []string{"10.0.0.1"}) {
		t.Fatalf("got %v, want only the tenant-app pod", got)
	}
}

func TestTheCacheFollowsPodsComingAndGoing(t *testing.T) {
	c, client := startCache(t)
	slices := client.DiscoveryV1().EndpointSlices("tenant-5")

	created, err := slices.Create(context.Background(),
		slice("tenant-5", "a", "tenant-app", ep("10.0.0.1", boolPtr(true))), metav1.CreateOptions{})
	if err != nil {
		t.Fatal(err)
	}
	eventually(t, "a new pod to appear", func() bool {
		return reflect.DeepEqual(c.Endpoints("tenant-5"), []string{"10.0.0.1"})
	})

	created.Endpoints = append(created.Endpoints, ep("10.0.0.2", boolPtr(true)))
	if _, err := slices.Update(context.Background(), created, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a second pod to appear", func() bool {
		return reflect.DeepEqual(c.Endpoints("tenant-5"), []string{"10.0.0.1", "10.0.0.2"})
	})

	created.Endpoints[0].Conditions.Ready = boolPtr(false) // the first pod fails its readiness probe
	if _, err := slices.Update(context.Background(), created, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	eventually(t, "the unready pod to be dropped", func() bool {
		return reflect.DeepEqual(c.Endpoints("tenant-5"), []string{"10.0.0.2"})
	})

	if err := slices.Delete(context.Background(), "a", metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	eventually(t, "the tenant's pods to disappear once it is deleted", func() bool {
		return len(c.Endpoints("tenant-5")) == 0
	})
}
