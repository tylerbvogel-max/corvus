import { useState, useCallback } from 'react'
import type { TreeNode } from '../types'
import { fetchChildren } from '../api/knowledge_graph';
import type { ConceptNeuron } from '../api/operator';

interface ConceptGroupProps {
  concepts: ConceptNeuron[];
  open: boolean;
  onToggle: () => void;
  search: string;
}

interface Props {
  nodes: TreeNode[];
  search: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onChildrenLoaded?: (parentId: number, children: TreeNode[]) => void;
  conceptGroup?: ConceptGroupProps;
  /** Force every branch open to its deepest loaded layer. */
  forceOpen?: boolean;
}

function matchesSearch(node: TreeNode, term: string): boolean {
  if (!term) return true;
  // Support id search (e.g. "#2576" or "2576")
  const idMatch = term.match(/^#?(\d+)$/);
  if (idMatch) {
    const targetId = Number(idMatch[1]);
    if (node.id === targetId) return true;
    return (node.children ?? []).some(c => matchesSearch(c, term));
  }
  const lower = term.toLowerCase();
  if (node.label.toLowerCase().includes(lower)) return true;
  return (node.children ?? []).some(c => matchesSearch(c, term));
}

function TreeNodeRow({
  node,
  search,
  selectedId,
  onSelect,
  onExpand,
  depth,
  forceOpen,
}: {
  node: TreeNode;
  search: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onExpand: (id: number) => Promise<void>;
  depth: number;
  forceOpen?: boolean;
}) {
  const children = node.children ?? [];
  const childCount = node.child_count ?? children.length;
  const hasChildren = childCount > 0;
  const childrenLoaded = node.children !== undefined;
  const searchMatch = search && matchesSearch(node, search);
  const [open, setOpen] = useState(depth < 1);
  const [loading, setLoading] = useState(false);

  // Auto-expand when a search matches a descendant, or when the
  // Explorer's expand-all mode is on (full tree is already fetched).
  const shouldAutoExpand = !!(search && searchMatch && hasChildren && childrenLoaded);
  const effectiveOpen = open || shouldAutoExpand || (!!forceOpen && childrenLoaded);

  if (search && !searchMatch) return null;

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!open && hasChildren && !childrenLoaded) {
      setLoading(true);
      await onExpand(node.id);
      setLoading(false);
    }
    setOpen(!open);
  };

  return (
    <div className="tree-node">
      <div
        className={`tree-row layer-${node.layer}${selectedId === node.id ? ' selected' : ''}`}
        style={{ paddingLeft: 12 + depth * 16 }}
        onClick={() => onSelect(node.id)}
      >
        <span className="tree-toggle" onClick={handleToggle}>
          {hasChildren ? (loading ? '\u23F3' : effectiveOpen ? '\u25BC' : '\u25B6') : ''}
        </span>
        <span className="tree-label">{node.label}</span>
        {node.invocations > 0 && <span className="tree-badge">{node.invocations}</span>}
      </div>
      {hasChildren && effectiveOpen && childrenLoaded && (
        <div className="tree-children">
          {children.map(child => (
            <TreeNodeRow
              key={child.id}
              node={child}
              search={search}
              selectedId={selectedId}
              onSelect={onSelect}
              onExpand={onExpand}
              depth={depth + 1}
              forceOpen={forceOpen}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function NeuronTree({ nodes, search, selectedId, onSelect, onChildrenLoaded, conceptGroup, forceOpen }: Props) {
  const handleExpand = useCallback(async (parentId: number) => {
    if (!onChildrenLoaded) return;
    try {
      const childNodes = await fetchChildren(parentId);
      const treeChildren: TreeNode[] = childNodes.map(c => ({
        id: c.id,
        layer: c.layer,
        node_type: c.node_type,
        label: c.label,
        department: c.department,
        role_key: c.role_key,
        invocations: c.invocations,
        avg_utility: c.avg_utility,
        child_count: c.child_count,
        // children left undefined until this node is expanded
      }));
      onChildrenLoaded(parentId, treeChildren);
    } catch (err) {
      console.error('Failed to load children:', err);
    }
  }, [onChildrenLoaded]);

  const cg = conceptGroup;
  const filteredConcepts = cg?.concepts.filter(c =>
    !cg.search || c.label.toLowerCase().includes(cg.search.toLowerCase()) || cg.search.replace(/^#/, '') === String(c.id)
  );
  // Auto-open concept group when search matches a concept
  const conceptVisible = cg && (!cg.search || (filteredConcepts && filteredConcepts.length > 0));

  return (
    <div className="tree-scroll">
      {conceptVisible && cg && (
        <div className="tree-node">
          <div className="tree-row concept-group-header" onClick={cg.onToggle}>
            <span className="tree-toggle">{cg.open || cg.search ? '\u25BC' : '\u25B6'}</span>
            <span className="tree-label" style={{ color: 'var(--layer-concept)', fontWeight: 600 }}>
              Concepts
            </span>
            <span className="tree-badge" style={{ background: '#e879f922', color: '#e879f9' }}>
              {cg.concepts.length}
            </span>
          </div>
          {(cg.open || cg.search) && filteredConcepts && (
            <div className="tree-children">
              {filteredConcepts.map(c => (
                <div key={c.id} className="tree-node">
                  <div
                    className={`tree-row layer-concept${selectedId === c.id ? ' selected' : ''}`}
                    style={{ paddingLeft: 28 }}
                    onClick={() => onSelect(c.id)}
                  >
                    <span className="tree-label">{c.label}</span>
                    {c.invocations > 0 && <span className="tree-badge">{c.invocations}</span>}
                    <span className="concept-edge-count">{c.instantiation_edges}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {nodes.map(node => (
        <TreeNodeRow
          key={node.id}
          node={node}
          search={search}
          selectedId={selectedId}
          onSelect={onSelect}
          onExpand={handleExpand}
          depth={0}
          forceOpen={forceOpen}
        />
      ))}
    </div>
  );
}
