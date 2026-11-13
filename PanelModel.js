// Shared controls own their Text items, so neutralize markup before passing
// device-provided names to those controls. Plugin-owned Text stays PlainText.
function safeLabel(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function outputOptions(outputs, selected, noneLabel, unavailableLabel, hdmiPattern) {
  var options = [{value: "", label: noneLabel}];
  var found = selected === "";
  for (var i = 0; i < outputs.length; i++) {
    var output = outputs[i];
    if (output.name === selected) found = true;
    if (!output.available && output.name !== selected) continue;
    var monitor = String(output.label).match(/HDMI.*\[([^\]]+)\]/);
    var label = monitor ? hdmiPattern.replace("%1", monitor[1]) : output.label;
    options.push({value: output.name, label: output.available ? safeLabel(label) : unavailableLabel});
  }
  if (!found) options.push({value: selected, label: unavailableLabel});
  return options;
}
