/* 知识图谱 D3 力导向渲染模块（与 Alpine 解耦）
 *
 * window.VaultGraph.render(svgEl, data, opts) -> { highlight(path), destroy() }
 *   data: { nodes:[{id,label,group,degree}], edges:[{source,target,weight}] }
 *   opts: { onSelect(path), showLabels:bool, currentPath:string|null }
 *
 * 颜色全部读自 :root 的 CSS 变量，主题切换时由调用方重新 render 即可自动适配。
 */
(function () {
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback || '#888';
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function render(svgEl, data, opts) {
    opts = opts || {};
    if (!window.d3) {
      console.error('[VaultGraph] d3 未加载');
      return { highlight() {}, destroy() {} };
    }
    const d3 = window.d3;

    // 颜色（render 时取一次，主题切换走重渲染）
    const C = {
      ink: cssVar('--ink', '#fff'),
      mute: cssVar('--mute', '#7d8187'),
      hairline: cssVar('--hairline', '#212327'),
      sunset: cssVar('--accent-sunset', '#ff7a17'),
      canvas: cssVar('--canvas', '#0a0a0a'),
    };
    // 分组 = 笔记所在目录（用户 taxonomy 动态决定，组数不定）。
    // 用均匀分布的 HSL 给每个目录一个稳定且互不相同的颜色（起点贴近品牌 sunset 色相）。
    const groups = Array.from(new Set(data.nodes.map(n => n.group).filter(Boolean))).sort();
    const colorOf = (g) => {
      if (!g) return C.mute;  // 顶层散落（无目录）/孤立 → 灰
      const i = groups.indexOf(g);
      if (i < 0) return C.mute;
      const hue = Math.round((25 + i * 360 / Math.max(groups.length, 1)) % 360);
      return `hsl(${hue}, 55%, 60%)`;
    };

    // 容器尺寸
    const rect = svgEl.getBoundingClientRect();
    const W = rect.width || svgEl.clientWidth || 320;
    const H = rect.height || svgEl.clientHeight || 240;

    // 清空旧内容
    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();
    svg.attr('viewBox', `0 0 ${W} ${H}`);

    const zoomG = svg.append('g').attr('class', 'graph-zoom');

    // 深拷贝（d3 力导向会写入坐标，避免污染原始数据）
    const nodes = data.nodes.map(n => Object.assign({}, n));
    const links = data.edges.map(e => Object.assign({}, e));

    // 邻接表（用于 highlight）
    const adj = new Map();
    nodes.forEach(n => adj.set(n.id, new Set([n.id])));
    links.forEach(l => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      adj.get(s) && adj.get(s).add(t);
      adj.get(t) && adj.get(t).add(s);
    });

    const radius = (n) => clamp(3 + (n.degree || 0) * 1.4, 3, 11);

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(60).strength(0.5))
      .force('charge', d3.forceManyBody().strength(nodes.length > 60 ? -60 : -140))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide().radius(d => radius(d) + 4))
      .force('x', d3.forceX(W / 2).strength(0.04))
      .force('y', d3.forceY(H / 2).strength(0.04));

    const link = zoomG.append('g')
      .attr('class', 'graph-edges')
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('class', 'graph-edge')
      .attr('stroke', C.hairline)
      .attr('stroke-width', d => clamp(0.6 + (d.weight || 1) * 0.4, 0.6, 3))
      .attr('stroke-opacity', d => clamp(0.35 + (d.weight || 1) * 0.12, 0.35, 0.9));

    const node = zoomG.append('g')
      .attr('class', 'graph-nodes')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .attr('class', 'graph-node')
      .call(drag(sim));

    node.append('circle')
      .attr('r', radius)
      .attr('fill', d => colorOf(d.group))
      .attr('fill-opacity', d => (d.degree ? 0.92 : 0.45))
      .attr('stroke', C.canvas)
      .attr('stroke-width', 1);

    const label = node.append('text')
      .attr('class', 'graph-label')
      .attr('x', d => radius(d) + 3)
      .attr('y', 3)
      .attr('fill', C.mute)
      .text(d => d.label)
      .style('display', opts.showLabels ? null : 'none');

    // 交互：点节点 → 回调
    node.style('cursor', 'pointer').on('click', (ev, d) => {
      ev.stopPropagation();
      if (opts.onSelect) opts.onSelect(d.id);
    });
    // hover 临时显示标签（小图未常显时）
    node.on('mouseenter', function (ev, d) {
      if (!opts.showLabels) d3.select(this).select('text').style('display', null);
    }).on('mouseleave', function (ev, d) {
      if (!opts.showLabels && !d._pinned) d3.select(this).select('text').style('display', 'none');
    });

    // 缩放/平移
    const zoom = d3.zoom().scaleExtent([0.3, 4]).on('zoom', (ev) => {
      zoomG.attr('transform', ev.transform);
    });
    svg.call(zoom).on('dblclick.zoom', null);

    sim.on('tick', () => {
      link
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    function drag(simulation) {
      return d3.drag()
        .on('start', (ev, d) => {
          if (!ev.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end', (ev, d) => {
          if (!ev.active) simulation.alphaTarget(0);
          d.fx = null; d.fy = null;
        });
    }

    function highlight(path) {
      const focusSet = path && adj.has(path) ? adj.get(path) : null;
      node.classed('graph-dim', d => focusSet ? !focusSet.has(d.id) : false);
      node.classed('graph-focus', d => focusSet ? d.id === path : false);
      // 高亮节点常显标签
      label.style('display', d => {
        if (opts.showLabels) return null;
        return (focusSet && focusSet.has(d.id)) ? null : 'none';
      });
      node.each(function (d) { d._pinned = !!(focusSet && focusSet.has(d.id)); });
      link.classed('graph-dim', d => {
        if (!focusSet) return false;
        const s = d.source.id || d.source, t = d.target.id || d.target;
        return !(focusSet.has(s) && focusSet.has(t));
      });
    }

    if (opts.currentPath) highlight(opts.currentPath);

    // 图例：顶层目录 → 颜色（与节点同款映射）；有孤立点则追加一项
    const legend = groups.map(g => ({label: g, color: colorOf(g)}));
    if (nodes.some(n => !n.degree)) legend.push({label: '孤立 · 暂无连接', color: C.mute, faded: true});

    return {
      legend,
      highlight,
      destroy() {
        sim.stop();
        svg.on('.zoom', null);
        svg.selectAll('*').remove();
      },
    };
  }

  window.VaultGraph = { render };
})();
