import { Box, DoorOpen, Layers3, Pause, Play, RectangleHorizontal, ScanLine } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import type { CargoInput, ContainerSpec, PackingSolution, Placement, Zone } from "../types";

type ViewMode = "3d" | "top" | "side" | "door" | "layers";

interface Props {
  container: ContainerSpec;
  solution: PackingSolution;
  cargoItems: CargoInput[];
  selectedCargoId?: string | null;
  onSelectCargo?: (cargoId: string | null) => void;
  onSnapshot?: (dataUrl: string) => void;
}

const PALETTE = ["#0b8f79", "#df8b2f", "#3375b8", "#c6534d", "#6d6eb5", "#568b48", "#b15888", "#4b8996", "#9c6a3c", "#78818c"];

export function cargoColorMap(cargoItems: CargoInput[]): Record<string, string> {
  return Object.fromEntries(cargoItems.map((cargo, index) => [cargo.id, PALETTE[index % PALETTE.length]]));
}

interface MeshGroup {
  placements: Placement[];
  mesh: THREE.InstancedMesh;
  outline: THREE.InstancedMesh;
  matrices: THREE.Matrix4[];
}

function ThreeScene({ container, placements, visibleStep, colors, selectedCargoId, onSelectCargo, onSnapshot }: {
  container: ContainerSpec;
  placements: Placement[];
  visibleStep: number;
  colors: Record<string, string>;
  selectedCargoId?: string | null;
  onSelectCargo?: (cargoId: string | null) => void;
  onSnapshot?: (dataUrl: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const groupsRef = useRef<MeshGroup[]>([]);
  const rendererRef = useRef<{ renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.Camera } | null>(null);
  const onSelectRef = useRef(onSelectCargo);
  const onSnapshotRef = useRef(onSnapshot);

  useEffect(() => { onSelectRef.current = onSelectCargo; }, [onSelectCargo]);
  useEffect(() => { onSnapshotRef.current = onSnapshot; }, [onSnapshot]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof window.WebGLRenderingContext === "undefined") return;

    const width = Math.max(host.clientWidth, 320);
    const height = Math.max(host.clientHeight, 320);
    const maxDimension = Math.max(container.inner_length_mm, container.inner_width_mm, container.inner_height_mm);
    const scale = 10 / maxDimension;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#e9eeea");
    scene.fog = new THREE.Fog("#e9eeea", 15, 34);

    const camera = new THREE.PerspectiveCamera(37, width / height, 0.01, 100);
    camera.position.set(12.5, 8.2, 10.8);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.dataset.layoutCanvas = "true";
    renderer.domElement.setAttribute("aria-label", "三维装柜布局图");
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.target.set(0, 0, 0);
    controls.minDistance = 4;
    controls.maxDistance = 32;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x7c8981, 2.4));
    const light = new THREE.DirectionalLight(0xffffff, 2.7);
    light.position.set(7, 12, 8);
    light.castShadow = true;
    scene.add(light);

    const containerGeometry = new THREE.BoxGeometry(
      container.inner_length_mm * scale,
      container.inner_height_mm * scale,
      container.inner_width_mm * scale,
    );
    const containerEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(containerGeometry),
      new THREE.LineBasicMaterial({ color: 0x58645e, transparent: true, opacity: 0.68 }),
    );
    scene.add(containerEdges);

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(container.inner_length_mm * scale, container.inner_width_mm * scale),
      new THREE.MeshStandardMaterial({ color: 0xdde3de, roughness: 1 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -container.inner_height_mm * scale / 2 - 0.005;
    floor.receiveShadow = true;
    scene.add(floor);

    const cargoMeshes: THREE.InstancedMesh[] = [];
    const grouped = new Map<string, Placement[]>();
    placements.forEach((placement) => {
      const group = grouped.get(placement.cargo_id) ?? [];
      group.push(placement);
      grouped.set(placement.cargo_id, group);
    });
    grouped.forEach((cargoPlacements, cargoId) => {
      const geometry = new THREE.BoxGeometry(1, 1, 1);
      const color = colors[cargoId] ?? "#7b8680";
      const material = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.72,
        metalness: 0.02,
        transparent: false,
        opacity: 0.92,
      });
      const mesh = new THREE.InstancedMesh(geometry, material, cargoPlacements.length);
      const outline = new THREE.InstancedMesh(
        geometry,
        new THREE.MeshBasicMaterial({ color: 0x18312a, wireframe: true, transparent: true, opacity: 0.32 }),
        cargoPlacements.length,
      );
      const matrix = new THREE.Matrix4();
      const position = new THREE.Vector3();
      const size = new THREE.Vector3();
      const quaternion = new THREE.Quaternion();
      const matrices: THREE.Matrix4[] = [];
      cargoPlacements.forEach((placement, index) => {
        position.set(
          (placement.x_mm + placement.length_mm / 2 - container.inner_length_mm / 2) * scale,
          (placement.z_mm + placement.height_mm / 2 - container.inner_height_mm / 2) * scale,
          (placement.y_mm + placement.width_mm / 2 - container.inner_width_mm / 2) * scale,
        );
        size.set(
          placement.length_mm * scale * 0.992,
          placement.height_mm * scale * 0.992,
          placement.width_mm * scale * 0.992,
        );
        matrix.compose(position, quaternion, size);
        mesh.setMatrixAt(index, matrix);
        outline.setMatrixAt(index, matrix);
        matrices.push(matrix.clone());
      });
      mesh.instanceMatrix.needsUpdate = true;
      outline.instanceMatrix.needsUpdate = true;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.cargoId = cargoId;
      scene.add(mesh);
      scene.add(outline);
      cargoMeshes.push(mesh);
      groupsRef.current.push({ placements: cargoPlacements, mesh, outline, matrices });
    });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handlePointer = (event: PointerEvent) => {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(cargoMeshes)[0];
      onSelectRef.current?.(hit ? String(hit.object.userData.cargoId) : null);
    };
    renderer.domElement.addEventListener("pointerdown", handlePointer);

    let frame = 0;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    };
    animate();
    requestAnimationFrame(() => {
      try { onSnapshotRef.current?.(renderer.domElement.toDataURL("image/png")); } catch { /* Canvas export can be blocked by browser policy. */ }
    });

    const observer = new ResizeObserver(() => {
      const nextWidth = Math.max(host.clientWidth, 320);
      const nextHeight = Math.max(host.clientHeight, 320);
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
    });
    observer.observe(host);
    rendererRef.current = { renderer, scene, camera };

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", handlePointer);
      controls.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) material.forEach((item) => item.dispose());
          else material.dispose();
        }
      });
      renderer.dispose();
      host.replaceChildren();
      groupsRef.current = [];
      rendererRef.current = null;
    };
  }, [container, placements, colors]);

  useEffect(() => {
    const hiddenMatrix = new THREE.Matrix4().makeScale(0, 0, 0);
    groupsRef.current.forEach((group) => {
      group.placements.forEach((placement, index) => {
        const matrix = placement.step <= visibleStep ? group.matrices[index] : hiddenMatrix;
        group.mesh.setMatrixAt(index, matrix);
        group.outline.setMatrixAt(index, matrix);
      });
      group.mesh.instanceMatrix.needsUpdate = true;
      group.outline.instanceMatrix.needsUpdate = true;
    });
    const state = rendererRef.current;
    if (state) {
      state.renderer.render(state.scene, state.camera);
      try { onSnapshotRef.current?.(state.renderer.domElement.toDataURL("image/png")); } catch { /* Browser may block canvas export. */ }
    }
  }, [visibleStep]);

  useEffect(() => {
    groupsRef.current.forEach((group) => {
      const cargoId = String(group.mesh.userData.cargoId);
      const dimmed = selectedCargoId != null && selectedCargoId !== cargoId;
      const material = group.mesh.material as THREE.MeshStandardMaterial;
      material.transparent = dimmed;
      material.opacity = dimmed ? 0.2 : 0.92;
      material.needsUpdate = true;
    });
  }, [selectedCargoId]);

  return <div className="three-scene" ref={hostRef} />;
}

export function StaticLayout({ mode, container, placements, zones, cargoItems, selectedCargoId, onSelectCargo, testId = "layout-svg" }: {
  mode: Exclude<ViewMode, "3d">;
  container: ContainerSpec;
  placements: Placement[];
  zones?: Zone[];
  cargoItems: CargoInput[];
  selectedCargoId?: string | null;
  onSelectCargo?: (cargoId: string | null) => void;
  testId?: string;
}) {
  const colors = cargoColorMap(cargoItems);
  const cargoById = Object.fromEntries(cargoItems.map((cargo) => [cargo.id, cargo]));
  const isTop = mode === "top" || mode === "layers";
  const totalWidth = isTop ? container.inner_length_mm : mode === "side" ? container.inner_length_mm : container.inner_width_mm;
  const totalHeight = isTop ? container.inner_width_mm : container.inner_height_mm;
  const fontSize = Math.max(totalWidth, totalHeight) / 42;
  const zoneKeys = new Set(placements.map((placement) => `${placement.cargo_id}:${placement.step}`));
  const visibleZones = (mode === "top" || mode === "layers" ? zones ?? [] : [])
    .filter((zone) => mode !== "layers" || zoneKeys.has(`${zone.cargo_id}:${zone.step}`));
  const showZones = visibleZones.length > 0 && visibleZones.length <= 30;
  return (
    <svg className="layout-svg" viewBox={`0 0 ${totalWidth} ${totalHeight}`} role="img" aria-label={`${mode} 装柜布局图`} data-testid={testId} data-visible-count={placements.length}>
      <rect width={totalWidth} height={totalHeight} fill="#f4f6f3" stroke="#59665f" strokeWidth={Math.max(totalWidth, totalHeight) / 300} />
      {placements.map((placement) => {
        const x = mode === "door" ? placement.y_mm : placement.x_mm;
        const y = isTop
          ? placement.y_mm
          : totalHeight - placement.z_mm - placement.height_mm;
        const width = mode === "door" ? placement.width_mm : placement.length_mm;
        const height = isTop ? placement.width_mm : placement.height_mm;
        const dimmed = selectedCargoId != null && selectedCargoId !== placement.cargo_id;
        return (
          <g key={placement.id} onClick={() => onSelectCargo?.(placement.cargo_id)} className="layout-item" opacity={dimmed ? 0.2 : 1}>
            <rect x={x} y={y} width={width} height={height} fill={colors[placement.cargo_id] ?? "#7b8680"} stroke="#173029" strokeWidth={Math.max(totalWidth, totalHeight) / 850} />
            {width > fontSize * 3 && height > fontSize * 1.5 && <text x={x + width / 2} y={y + height / 2} dominantBaseline="middle" textAnchor="middle" fontSize={fontSize} fill="white">{cargoById[placement.cargo_id]?.sku ?? placement.cargo_id}</text>}
          </g>
        );
      })}
      {showZones && visibleZones.map((zone) => (
        <g key={`zone-${zone.step}-${zone.cargo_id}-${zone.x_mm}-${zone.y_mm}`} className="layout-zone" data-testid="layout-zone">
          <rect x={zone.x_mm} y={zone.y_mm} width={zone.length_mm} height={zone.width_mm} className="zone-outline" />
          <circle className="zone-badge" cx={zone.x_mm + fontSize * 1.4} cy={zone.y_mm + fontSize * 1.4} r={fontSize * 0.9} />
          <text className="zone-badge-text" x={zone.x_mm + fontSize * 1.4} y={zone.y_mm + fontSize * 1.4} dominantBaseline="middle" textAnchor="middle" fontSize={fontSize} fill="white">{zone.step}</text>
          {zone.length_mm > fontSize * 3.5 && zone.width_mm > fontSize * 2 && (
            <text x={zone.x_mm + zone.length_mm / 2} y={zone.y_mm + zone.width_mm / 2 + fontSize * 1.3} textAnchor="middle" dominantBaseline="middle" fontSize={fontSize * 1.1} fontWeight="bold" fill="#173029" stroke="#ffffff" strokeWidth={fontSize * 0.22} paintOrder="stroke">
              {cargoById[zone.cargo_id]?.sku ?? zone.cargo_id} ×{zone.piece_count}
            </text>
          )}
        </g>
      ))}
      {mode === "door" && <text x={totalWidth / 2} y={fontSize * 1.2} textAnchor="middle" fontSize={fontSize} fill="#46534d">柜门视角</text>}
    </svg>
  );
}

export function LoadVisualizer({ container, solution, cargoItems, selectedCargoId, onSelectCargo, onSnapshot }: Props) {
  const [mode, setMode] = useState<ViewMode>("3d");
  const maxStep = Math.max(1, ...solution.placements.map((item) => item.step));
  const [step, setStep] = useState(maxStep);
  const layers = useMemo(
    () => [...new Set(solution.placements.map((item) => item.z_mm))].sort((a, b) => a - b),
    [solution.placements],
  );
  const [layerIndex, setLayerIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const colors = useMemo(() => cargoColorMap(cargoItems), [cargoItems]);

  useEffect(() => { setStep(maxStep); setPlaying(false); setLayerIndex(0); }, [solution.profile, maxStep]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setStep((current) => current >= maxStep ? 1 : current + 1);
    }, 850);
    return () => window.clearInterval(timer);
  }, [playing, maxStep]);

  const visible = useMemo(() => {
    const byStep = solution.placements.filter((item) => item.step <= step);
    return mode === "layers" ? byStep.filter((item) => item.z_mm === layers[layerIndex]) : byStep;
  }, [solution.placements, step, mode, layers, layerIndex]);
  const modes: Array<{ id: ViewMode; label: string; icon: typeof Box }> = [
    { id: "3d", label: "3D", icon: Box },
    { id: "top", label: "俯视", icon: RectangleHorizontal },
    { id: "side", label: "侧视", icon: ScanLine },
    { id: "door", label: "柜门", icon: DoorOpen },
    { id: "layers", label: "分层", icon: Layers3 },
  ];

  return (
    <div className="load-visualizer">
      <div className="visual-controls no-print">
        <div className="view-switcher" role="group" aria-label="布局视图">
          {modes.map(({ id, label, icon: Icon }) => <button key={id} type="button" className={mode === id ? "is-active" : ""} aria-label={label} title={label} aria-pressed={mode === id} onClick={() => setMode(id)}><Icon size={16} /><span>{label}</span></button>)}
        </div>
        <div className="step-control">
          <button type="button" className="icon-button" aria-label={playing ? "暂停装载演示" : "播放装载演示"} title={playing ? "暂停" : "播放"} onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>
          <label><span>步骤 {step}/{maxStep}</span><input aria-label="装载步骤" type="range" min="1" max={maxStep} value={step} onChange={(event) => setStep(Number(event.target.value))} /></label>
        </div>
      </div>

      <div className="visual-stage">
        {mode === "3d" ? <ThreeScene container={container} placements={solution.placements} visibleStep={step} colors={colors} selectedCargoId={selectedCargoId} onSelectCargo={onSelectCargo} onSnapshot={onSnapshot} /> : <StaticLayout mode={mode} container={container} placements={visible} zones={solution.zones} cargoItems={cargoItems} selectedCargoId={selectedCargoId} onSelectCargo={onSelectCargo} />}
      </div>

      {mode === "layers" && layers.length > 0 && <label className="layer-control no-print"><span>层高 {(layers[layerIndex] / 10).toFixed(1)} cm</span><input aria-label="查看层高" type="range" min="0" max={Math.max(0, layers.length - 1)} value={layerIndex} onChange={(event) => setLayerIndex(Number(event.target.value))} /></label>}
      <div className="cargo-legend no-print">
        {cargoItems.map((cargo) => <button key={cargo.id} type="button" className={selectedCargoId === cargo.id ? "is-active" : ""} onClick={() => onSelectCargo?.(selectedCargoId === cargo.id ? null : cargo.id)}><i style={{ background: colors[cargo.id] }} />{cargo.sku}</button>)}
      </div>
    </div>
  );
}
