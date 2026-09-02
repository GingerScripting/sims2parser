import SwiftUI
import AppKit
import SceneKit

/// The Preview tab: a decoded texture or a mesh, fetched from the daemon
/// when the tab opens. The daemon does the decoding; Swift only shows a
/// PNG or parses OBJ text into SceneKit geometry.
struct PreviewPane: View {
    @ObservedObject var session: PackageSession
    let detail: ResourceDetail

    @State private var texture: TexturePreview?
    @State private var mesh: MeshPreview?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        Group {
            if loading {
                ProgressView("Decoding…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let error {
                VStack(spacing: 8) {
                    Image(systemName: "eye.slash").font(.largeTitle).foregroundStyle(.secondary)
                    Text(error).multilineTextAlignment(.center).textSelection(.enabled)
                }
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let t = texture {
                textureView(t)
            } else if let m = mesh {
                meshView(m)
            } else {
                Color.clear
            }
        }
        .task(id: detail.tgi) { await load() }
    }

    private func load() async {
        loading = true
        error = nil
        texture = nil
        mesh = nil
        do {
            if detail.row.isTexture {
                texture = try await session.previewTexture(detail.tgi)
            } else if detail.row.isMesh {
                mesh = try await session.previewMesh(detail.tgi)
            }
        } catch is CancellationError {
            return                      // superseded by a newer selection
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }

    private func textureView(_ t: TexturePreview) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text(t.name).font(.headline).lineLimit(1)
                Text("\(t.width)×\(t.height) \(t.format), \(t.levels) mip levels").foregroundStyle(.secondary)
                if t.shownWidth != t.width {
                    Text("(showing \(t.shownWidth)×\(t.shownHeight))").foregroundStyle(.tertiary)
                }
                Spacer()
                Button("Export PNG…") {
                    if let url = SavePanels.chooseExport(suggested: "\(t.name).png") {
                        Task { await session.exportTexture(detail.tgi, to: url) }
                    }
                }
            }
            .font(.callout)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            Divider()
            ScrollView([.horizontal, .vertical]) {
                if let data = Data(base64Encoded: t.pngB64), let img = NSImage(data: data) {
                    Image(nsImage: img)
                        .interpolation(.none)
                        .background(CheckerboardBackground())
                        .padding(16)
                } else {
                    Text("Could not decode the PNG.").padding()
                }
            }
        }
    }

    private func meshView(_ m: MeshPreview) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text(m.name).font(.headline).lineLimit(1)
                Text("\(m.faces) faces in \(m.groups.count) group\(m.groups.count == 1 ? "" : "s")").foregroundStyle(.secondary)
                Spacer()
                Button("Export OBJ…") {
                    if let url = SavePanels.chooseExport(suggested: "\(m.name).obj") {
                        try? m.obj.write(to: url, atomically: true, encoding: .utf8)
                    }
                }
            }
            .font(.callout)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            Divider()
            MeshSceneView(obj: m.obj)
            Divider()
            HStack {
                ForEach(m.groups) { g in
                    Text("\(g.name) (\(g.faces))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if m.partial {
                    Text("The GMDC reader is partial; bounds and bone data are not read.")
                        .font(.caption).foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
        }
    }
}

/// Grey checkerboard behind textures so alpha is visible.
struct CheckerboardBackground: View {
    var body: some View {
        Canvas { ctx, size in
            let s: CGFloat = 8
            var y: CGFloat = 0
            while y < size.height {
                var x: CGFloat = 0
                while x < size.width {
                    let dark = (Int(x / s) + Int(y / s)) % 2 == 0
                    ctx.fill(Path(CGRect(x: x, y: y, width: s, height: s)),
                             with: .color(dark ? Color.gray.opacity(0.35) : Color.gray.opacity(0.15)))
                    x += s
                }
                y += s
            }
        }
    }
}

/// A SceneKit view of Wavefront OBJ text. Every face corner becomes its own
/// vertex, so groups on different vertex arrays never alias each other.
struct MeshSceneView: NSViewRepresentable {
    let obj: String

    func makeNSView(context: Context) -> SCNView {
        let view = SCNView()
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.backgroundColor = .windowBackgroundColor
        view.scene = Self.scene(from: obj)
        return view
    }

    func updateNSView(_ nsView: SCNView, context: Context) {
        if context.coordinator.obj != obj {
            context.coordinator.obj = obj
            nsView.scene = Self.scene(from: obj)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(obj: obj) }

    final class Coordinator {
        var obj: String
        init(obj: String) { self.obj = obj }
    }

    static func scene(from obj: String) -> SCNScene {
        var positions: [SCNVector3] = []
        var normals: [SCNVector3] = []
        var uvs: [CGPoint] = []
        var outPos: [SCNVector3] = []
        var outNorm: [SCNVector3] = []
        var outUV: [CGPoint] = []
        var indices: [Int32] = []

        func corner(_ token: Substring) {
            let parts = token.split(separator: "/", omittingEmptySubsequences: false)
            guard let vi = Int(parts[0]), vi > 0, vi <= positions.count else { return }
            outPos.append(positions[vi - 1])
            if parts.count > 1, let ti = Int(parts[1]), ti > 0, ti <= uvs.count { outUV.append(uvs[ti - 1]) }
            else { outUV.append(.zero) }
            if parts.count > 2, let ni = Int(parts[2]), ni > 0, ni <= normals.count { outNorm.append(normals[ni - 1]) }
            else { outNorm.append(SCNVector3(0, 0, 0)) }
            indices.append(Int32(outPos.count - 1))
        }

        for line in obj.split(separator: "\n", omittingEmptySubsequences: true) {
            let f = line.split(separator: " ", omittingEmptySubsequences: true)
            guard let head = f.first else { continue }
            switch head {
            case "v" where f.count >= 4:
                positions.append(SCNVector3(Double(f[1]) ?? 0, Double(f[2]) ?? 0, Double(f[3]) ?? 0))
            case "vn" where f.count >= 4:
                normals.append(SCNVector3(Double(f[1]) ?? 0, Double(f[2]) ?? 0, Double(f[3]) ?? 0))
            case "vt" where f.count >= 3:
                uvs.append(CGPoint(x: Double(f[1]) ?? 0, y: Double(f[2]) ?? 0))
            case "f" where f.count >= 4:
                // Fan-triangulate anything wider than a triangle.
                let corners = Array(f[1...])
                for k in 1..<(corners.count - 1) {
                    corner(corners[0]); corner(corners[k]); corner(corners[k + 1])
                }
            default:
                continue
            }
        }

        let scene = SCNScene()
        guard !outPos.isEmpty else { return scene }
        var sources = [SCNGeometrySource(vertices: outPos)]
        if outNorm.contains(where: { $0.x != 0 || $0.y != 0 || $0.z != 0 }) {
            sources.append(SCNGeometrySource(normals: outNorm))
        }
        sources.append(SCNGeometrySource(textureCoordinates: outUV))
        let element = SCNGeometryElement(indices: indices, primitiveType: .triangles)
        let geometry = SCNGeometry(sources: sources, elements: [element])
        let material = SCNMaterial()
        material.diffuse.contents = NSColor.systemGray
        material.isDoubleSided = true
        geometry.materials = [material]
        let node = SCNNode(geometry: geometry)
        scene.rootNode.addChildNode(node)

        // Frame the mesh: camera back along +Z at a distance set by its bounds.
        let (minB, maxB) = node.boundingBox
        let center = SCNVector3((minB.x + maxB.x) / 2, (minB.y + maxB.y) / 2, (minB.z + maxB.z) / 2)
        let radius = max(maxB.x - minB.x, maxB.y - minB.y, maxB.z - minB.z)
        let camera = SCNNode()
        camera.camera = SCNCamera()
        camera.camera?.zNear = 0.01
        camera.position = SCNVector3(center.x + radius * 0.8, center.y + radius * 0.6, center.z + radius * 1.6)
        camera.look(at: center)
        scene.rootNode.addChildNode(camera)
        return scene
    }
}
