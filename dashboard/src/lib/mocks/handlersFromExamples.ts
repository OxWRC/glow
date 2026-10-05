/**
 * Example-driven MSW handlers.
 *
 * These handlers use contract examples from the API directory as their single source of truth.
 * This ensures frontend mocks stay in sync with backend contracts.
 */

import { http, HttpResponse, delay } from "msw";
import { getExample, matchQueryExample } from "./contractExamples";

const API_BASE = "/api";

/**
 * Configuration for selecting which examples to use per endpoint.
 * This can be overridden in stories via the apiResponses parameter.
 */
export interface ApiResponseConfig {
  "POST /auth/login"?: string;
  "GET /admin/me"?: string;
  "GET /me"?: string;
  "GET /dimensions"?: string;
  "GET /query"?: string;
  "POST /api/query"?: string;
}

/**
 * Create MSW handlers from example configuration.
 */
export function createHandlersFromExamples(
  config: ApiResponseConfig = {},
): Array<ReturnType<typeof http.get | typeof http.post>> {
  const handlers: Array<ReturnType<typeof http.get | typeof http.post>> = [];

  // Login handler
  const loginExampleId = config["POST /auth/login"] || "auth.login.success";
  const loginExample = getExample(loginExampleId);
  if (loginExample) {
    handlers.push(
      http.post("/auth/login", async () => {
        await delay(300);
        return HttpResponse.json(loginExample.response, {
          status: loginExample.status,
        });
      }),
    );
  }

  // Current user handler
  const meExampleId = config["GET /admin/me"] || "admin.me.user";
  const meExample = getExample(meExampleId);
  if (meExample) {
    handlers.push(
      http.get(`${API_BASE}/admin/me`, async () => {
        await delay(100);
        return HttpResponse.json(meExample.response, {
          status: meExample.status,
        });
      }),
    );
  }

  // Identity endpoint handler (/me)
  const identityExampleId = config["GET /me"] || "me.authenticated";
  const identityExample = getExample(identityExampleId);
  if (identityExample) {
    handlers.push(
      http.get(`${API_BASE}/me`, async () => {
        await delay(100);
        return HttpResponse.json(identityExample.response, {
          status: identityExample.status,
        });
      }),
    );
  }

  // Dimensions endpoint handler
  const dimensionsExampleId = config["GET /dimensions"] || "dimensions.dataset";
  const dimensionsExample = getExample(dimensionsExampleId);
  if (dimensionsExample) {
    handlers.push(
      http.get(`${API_BASE}/dimensions`, async () => {
        await delay(150);
        // If a school-scoped example is requested, try to find it
        // Otherwise use the configured or default example
        return HttpResponse.json(dimensionsExample.response, {
          status: dimensionsExample.status,
        });
      }),
    );
  }

  // Demo banner endpoints
  const demoInfoExample = getExample("demo.info");
  if (demoInfoExample) {
    handlers.push(
      http.get(`${API_BASE}/demo/info`, () =>
        HttpResponse.json(demoInfoExample.response, {
          status: demoInfoExample.status,
        }),
      ),
    );
  }
  handlers.push(
    http.post(
      `${API_BASE}/demo/reset`,
      () => new HttpResponse(null, { status: 204 }),
    ),
  );

  // Query handler (GET /query)
  const getQueryExampleId = config["GET /query"];
  if (getQueryExampleId) {
    const getQueryExample = getExample(getQueryExampleId);
    if (getQueryExample) {
      handlers.push(
        http.get(`${API_BASE}/query`, async () => {
          await delay(500);
          return HttpResponse.json(getQueryExample.response, {
            status: getQueryExample.status,
          });
        }),
      );
    }
  }

  // Query handler
  const queryExampleId = config["POST /api/query"];
  handlers.push(
    http.post(`${API_BASE}/query`, async ({ request }) => {
      await delay(500);
      const body = await request.json();
      const example = matchQueryExample(body, queryExampleId);

      if (example) {
        return HttpResponse.json(example.response, { status: example.status });
      }

      // Fallback to default
      const fallback = getExample("query.default");
      return HttpResponse.json(fallback?.response || {}, {
        status: fallback?.status || 200,
      });
    }),
  );

  return handlers;
}

/**
 * Default handlers using the standard "happy path" examples.
 */
export const handlers = createHandlersFromExamples();

/**
 * Behavior-only handlers for edge cases that can't be represented as static examples.
 */
export const behaviorHandlers = {
  /**
   * Handler that never resolves (for testing loading states).
   */
  loading: http.get(`${API_BASE}/query`, async () => {
    await delay("infinite");
    return HttpResponse.json({});
  }),

  /**
   * Handler that returns malformed JSON (for testing error handling).
   */
  malformedJson: http.get(`${API_BASE}/query`, async () => {
    await delay(300);
    return new HttpResponse("not valid json{", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }),
};
