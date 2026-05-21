/**
 * Local types for the Google Gmail API wrapper.
 */

export interface GoogleAuthorizedUser {
  type?: string;
  client_id: string;
  client_secret: string;
  refresh_token: string;
  access_token?: string;
  expiry_date?: number;
  scopes?: string[];
  scope?: string;
}

export interface BatchModifyItem {
  messageId: string;
  addLabelIds: string[];
  removeLabelIds: string[];
}

export interface GroupedBatch {
  addLabelIds: string[];
  removeLabelIds: string[];
  ids: string[];
}

/**
 * Group batch-modify items by identical (addLabelIds, removeLabelIds) pair so
 * each unique combination becomes a single batchModify call.
 */
export function groupBatchItems(items: BatchModifyItem[]): GroupedBatch[] {
  const buckets = new Map<string, GroupedBatch>();
  for (const item of items) {
    const addSorted = [...item.addLabelIds].sort();
    const removeSorted = [...item.removeLabelIds].sort();
    const key = JSON.stringify([addSorted, removeSorted]);
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { addLabelIds: addSorted, removeLabelIds: removeSorted, ids: [] };
      buckets.set(key, bucket);
    }
    bucket.ids.push(item.messageId);
  }
  return [...buckets.values()];
}
