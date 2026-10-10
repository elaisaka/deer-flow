import type { ThreadState } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";

import type { AgentThread, AgentThreadState } from "./types";

// Keep only the fixtures exercised by public artifact and auth regressions.
export const DEMO_THREAD_IDS = [
  "7cfa5f8f-a2f8-47ad-acbd-da7137baf990",
  "3823e443-4e2b-4679-b496-a9506eae462b",
] as const;

export const SHOWCASE_ROUTE_PREFIX = "/showcase";

export const STATIC_DEMO_ARTIFACTS: Readonly<
  Record<string, readonly string[]>
> = {
  "3823e443-4e2b-4679-b496-a9506eae462b": [
    "user-data/outputs/fei-fei-li-podcast-timeline.md",
  ],
  "7cfa5f8f-a2f8-47ad-acbd-da7137baf990": [
    "user-data/outputs/index.html",
    "user-data/outputs/script.js",
    "user-data/outputs/style.css",
  ],
};

const STATIC_DEMO_ARTIFACT_SETS = Object.fromEntries(
  Object.entries(STATIC_DEMO_ARTIFACTS).map(([threadId, artifacts]) => [
    threadId,
    new Set(artifacts),
  ]),
) as Readonly<Record<string, ReadonlySet<string>>>;

export function resolveStaticDemoArtifact(
  threadId: string,
  encodedSegments: readonly string[],
): string | null {
  const allowedArtifacts = STATIC_DEMO_ARTIFACT_SETS[threadId];
  if (!allowedArtifacts || encodedSegments[0] !== "mnt") return null;

  let segments: string[];
  try {
    segments = encodedSegments.map((segment) => decodeURIComponent(segment));
  } catch {
    return null;
  }
  if (
    segments.some(
      (segment) =>
        segment.length === 0 ||
        segment === "." ||
        segment === ".." ||
        segment.includes("/") ||
        segment.includes("\\"),
    )
  ) {
    return null;
  }

  const artifactPath = segments.slice(1).join("/");
  if (!allowedArtifacts.has(artifactPath)) return null;
  return `/demo/threads/${threadId}/${artifactPath}`;
}

const DEMO_THREAD_ID_SET = new Set<string>(DEMO_THREAD_IDS);

export function isDemoThreadId(threadId: string): boolean {
  return DEMO_THREAD_ID_SET.has(threadId);
}

export function pathOfPublicDemoThread(threadId: string): string {
  return `${SHOWCASE_ROUTE_PREFIX}/${encodeURIComponent(threadId)}`;
}

export type ThreadSearchParams = NonNullable<
  Parameters<ThreadsClient["search"]>[0]
>;

export async function loadStaticDemoThreads(
  params: ThreadSearchParams = {},
): Promise<AgentThread[]> {
  const threads = await Promise.all(
    DEMO_THREAD_IDS.map((threadId) => loadStaticDemoThread(threadId)),
  );

  const sortBy = params.sortBy ?? "updated_at";
  const sortOrder = params.sortOrder ?? "desc";
  const sortedThreads = [...threads].sort((a, b) => {
    const aTimestamp = (a as unknown as Record<string, unknown>)[sortBy];
    const bTimestamp = (b as unknown as Record<string, unknown>)[sortBy];
    const aParsed = typeof aTimestamp === "string" ? Date.parse(aTimestamp) : 0;
    const bParsed = typeof bTimestamp === "string" ? Date.parse(bTimestamp) : 0;
    const aValue = Number.isNaN(aParsed) ? 0 : aParsed;
    const bValue = Number.isNaN(bParsed) ? 0 : bParsed;
    return sortOrder === "asc" ? aValue - bValue : bValue - aValue;
  });

  const offset = Math.max(0, Math.floor(params.offset ?? 0));
  const limit =
    typeof params.limit === "number"
      ? Math.max(0, Math.floor(params.limit))
      : sortedThreads.length;
  return sortedThreads.slice(offset, offset + limit);
}

export async function loadStaticDemoThread(
  threadId: string,
): Promise<AgentThread> {
  const response = await globalThis.fetch(
    `/demo/threads/${encodeURIComponent(threadId)}/thread.json`,
  );
  if (!response.ok) {
    throw new Error(`Failed to load demo thread ${threadId}`);
  }
  const thread = (await response.json()) as AgentThread;
  return {
    ...thread,
    thread_id: threadId,
    updated_at: thread.updated_at ?? thread.created_at,
  };
}

export function staticDemoThreadState(
  thread: AgentThread,
): ThreadState<AgentThreadState> {
  return {
    values: thread.values,
    next: [],
    checkpoint: {
      thread_id: thread.thread_id,
      checkpoint_ns: "",
      checkpoint_id: null,
      checkpoint_map: null,
    },
    metadata: thread.metadata ?? null,
    created_at: thread.updated_at ?? thread.created_at ?? null,
    parent_checkpoint: null,
    tasks: [],
  };
}
