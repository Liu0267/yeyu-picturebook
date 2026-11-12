import Foundation
import Vision
import AppKit
struct Line: Codable {let text: String; let confidence: Float; let bbox: [Double]}
struct Result: Codable {let path: String; let engine: String; let lines: [Line]; let error: String?}
for path in CommandLine.arguments.dropFirst() {
    do {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        try VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:]).perform([request])
        let lines = (request.results ?? []).compactMap { observation -> Line? in
            guard let c = observation.topCandidates(1).first else {return nil}
            let b = observation.boundingBox
            return Line(text:c.string, confidence:c.confidence, bbox:[b.minX, 1-b.maxY, b.maxX, 1-b.minY])
        }.sorted { abs($0.bbox[1] - $1.bbox[1]) > 0.012 ? $0.bbox[1] < $1.bbox[1] : $0.bbox[0] < $1.bbox[0] }
        let result = Result(path:path, engine:"apple_vision_\(request.revision)", lines:lines, error:nil)
        print(String(data:try JSONEncoder().encode(result), encoding:.utf8)!)
    } catch {
        print(String(data:try! JSONEncoder().encode(Result(path:path, engine:"apple_vision", lines:[], error:error.localizedDescription)), encoding:.utf8)!)
    }
}
