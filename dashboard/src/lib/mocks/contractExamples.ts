/**
 * Contract examples registry - loads examples from API directory.
 *
 * Examples are the single source of truth for API contracts, owned by the API package.
 * Dashboard MSW handlers consume these examples to ensure consistency.
 */

import { contractExamplesData } from "./contractExamplesData";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface ContractExample {
  id: string;
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  path: string;
  status: number;
  request_model: string | null;
  response_model: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  request: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  response: any;
}

// Build example registry indexed by ID
const exampleRegistry = new Map<string, ContractExample>();

for (const example of contractExamplesData as unknown as ContractExample[]) {
  exampleRegistry.set(example.id, example);
}

/**
 * Get a contract example by ID.
 */
export function getExample(id: string): ContractExample | undefined {
  return exampleRegistry.get(id);
}

/**
 * Get all contract examples.
 */
export function getAllExamples(): ContractExample[] {
  return Array.from(exampleRegistry.values());
}

/**
 * Get examples for a specific endpoint (method + path).
 */
export function getExamplesForEndpoint(
  method: string,
  path: string,
): ContractExample[] {
  return getAllExamples().filter(
    (ex) => ex.method === method && ex.path === path,
  );
}

/**
 * Match a POST request body against examples to find the right scenario.
 * Used for POST /api/query which has different responses based on request body.
 */
export function matchQueryExample(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  _requestBody: any,
  exampleId?: string,
): ContractExample | undefined {
  // If specific example ID provided, use it
  if (exampleId) {
    return getExample(exampleId);
  }

  // Otherwise, use default query example
  return getExample("query.default");
}
