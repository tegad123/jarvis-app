// The automatic JSX runtime, on top of the page's React.
var React = window.React
function jsx(type, props, key) {
  return React.createElement(type, key === undefined ? props : Object.assign({}, props, { key: key }))
}
module.exports = { jsx: jsx, jsxs: jsx, Fragment: React.Fragment }
