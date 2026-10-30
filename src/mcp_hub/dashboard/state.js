// State shared between modules, plus a refresh hook so feature modules never import app.js.
export const shared = {
  latestStatus: null,
  activityStreamId: null,
  lastActivitySequence: 0,
};

let refreshHandler = async () => {};

export function setRefreshHandler(handler) {
  refreshHandler = handler;
}

export function requestRefresh() {
  return refreshHandler();
}
