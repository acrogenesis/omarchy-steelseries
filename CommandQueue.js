function volumeKey(request) {
  if (request.action === "volume") return "volume:" + request.channel;
  if (request.action === "fallbackVolume") return "fallback:" + request.output;
  return "";
}

function enqueue(queue, request) {
  var result = queue.slice();
  var key = volumeKey(request);
  if (key) {
    for (var i = result.length - 1; i >= 0; i--) {
      var previous = volumeKey(result[i]);
      if (!previous) break; // Preserve ordering around output/setting changes.
      if (previous === key) {
        result.splice(i, 1);
        result.push(request);
        return result;
      }
    }
  }
  if (result.length < 32) result.push(request);
  return result;
}
