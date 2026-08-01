(() => {
  'use strict';

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    let body = {};
    try {
      body = await response.json();
    } catch (_) {
      // Keep the stable generic message for an empty or non-JSON error response.
    }
    if (!response.ok) {
      const message = body && typeof body === 'object' && body.error
        ? String(body.error)
        : '请求失败';
      throw new ApiError(message, response.status);
    }
    return body;
  }

  window.TaxpayerAPI = Object.freeze({
    ApiError,
    requestJson,
  });
})();
