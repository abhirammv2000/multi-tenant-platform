import http from 'k6/http';
import { check } from 'k6';

// Each virtual user keeps its own cookie jar, so after its first request it
// should stay on one pod. Users alternate between the two dummy tenants.
export const options = {
  vus: Number(__ENV.VUS || 50),
  duration: __ENV.DURATION || '30s',
};

export default function () {
  const tenant = 901 + (__VU % 2);
  const res = http.get(`${__ENV.BASE}/t/${tenant}/`);
  check(res, { 'status is 200': (r) => r.status === 200 });
}
