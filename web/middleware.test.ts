/**
 * Basic auth is the entire authentication story for this deployment — the API
 * has no public address, so if this is wrong nothing else catches it. Hence the
 * coverage of the ugly cases: no header, wrong scheme, malformed base64, a
 * password containing a colon, and near-miss credentials.
 */

import { describe, expect, it, beforeEach, afterEach } from "vitest";

import { middleware } from "./middleware";

const USER = "tejas";
const PASSWORD = "correct-horse:battery";

function request(authorization?: string) {
  const headers = new Headers();
  if (authorization) headers.set("authorization", authorization);
  return { headers } as unknown as Parameters<typeof middleware>[0];
}

function basic(user: string, password: string): string {
  return `Basic ${btoa(`${user}:${password}`)}`;
}

describe("when credentials are configured", () => {
  beforeEach(() => {
    process.env.APP_USERNAME = USER;
    process.env.APP_PASSWORD = PASSWORD;
  });
  afterEach(() => {
    delete process.env.APP_USERNAME;
    delete process.env.APP_PASSWORD;
  });

  it("accepts the correct credentials", () => {
    const response = middleware(request(basic(USER, PASSWORD)));
    expect(response.status).toBe(200);
  });

  it("accepts a password containing a colon", () => {
    // Splitting on every colon rather than the first would break this.
    expect(middleware(request(basic(USER, PASSWORD))).status).toBe(200);
  });

  it("rejects a missing header with a challenge", () => {
    const response = middleware(request());
    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("Basic");
  });

  it("never caches a 401", () => {
    expect(middleware(request()).headers.get("cache-control")).toBe("no-store");
  });

  it.each([
    ["wrong password", basic(USER, "nope")],
    ["wrong username", basic("someone", PASSWORD)],
    ["both wrong", basic("someone", "nope")],
    ["empty credentials", basic("", "")],
    ["password as username", basic(PASSWORD, USER)],
  ])("rejects %s", (_label, header) => {
    expect(middleware(request(header)).status).toBe(401);
  });

  it.each([
    ["a bearer token", "Bearer abc123"],
    ["no scheme", btoa(`${USER}:${PASSWORD}`)],
    ["lowercase scheme", `basic ${btoa(`${USER}:${PASSWORD}`)}`],
    ["malformed base64", "Basic !!!not-base64!!!"],
    ["no colon in the payload", `Basic ${btoa("justausername")}`],
    ["empty", "Basic "],
  ])("rejects %s", (_label, header) => {
    expect(middleware(request(header)).status).toBe(401);
  });

  it("rejects a prefix of the real password", () => {
    // The case a length-short-circuiting comparison would leak.
    expect(middleware(request(basic(USER, PASSWORD.slice(0, -1)))).status).toBe(401);
  });

  it("rejects a password with extra trailing characters", () => {
    expect(middleware(request(basic(USER, `${PASSWORD}x`))).status).toBe(401);
  });
});

describe("when credentials are not configured", () => {
  beforeEach(() => {
    delete process.env.APP_USERNAME;
    delete process.env.APP_PASSWORD;
  });

  it("runs open, for local development", () => {
    expect(middleware(request()).status).toBe(200);
  });

  it("stays open if only a username is set", () => {
    // Half-configured must not look protected while accepting anything.
    process.env.APP_USERNAME = USER;
    expect(middleware(request()).status).toBe(200);
    delete process.env.APP_USERNAME;
  });
});
