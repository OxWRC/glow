/**
 * Storybook MSW integration helpers for contract examples.
 *
 * Provides utilities to easily configure stories with contract examples.
 */

import {
  createHandlersFromExamples,
  type ApiResponseConfig,
  behaviorHandlers,
} from "./handlersFromExamples";

/**
 * Create MSW parameter configuration for a story using contract examples.
 *
 * @example
 * ```ts
 * export const MyStory: Story = {
 *   parameters: {
 *     msw: withApiResponses({
 *       'GET /admin/me': 'admin.me.admin',
 *       'POST /api/query': 'query.suppressed-focus',
 *     })
 *   }
 * }
 * ```
 */
export function withApiResponses(config: ApiResponseConfig) {
  return {
    handlers: createHandlersFromExamples(config),
  };
}

/**
 * Create MSW parameters for loading state (query never resolves).
 */
export function withLoadingState() {
  return {
    handlers: [behaviorHandlers.loading, ...createHandlersFromExamples()],
  };
}

/**
 * Create MSW parameters for malformed JSON response.
 */
export function withMalformedJson() {
  return {
    handlers: [behaviorHandlers.malformedJson, ...createHandlersFromExamples()],
  };
}
