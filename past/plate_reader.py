import cv2
import easyocr
from ultralytics import YOLO

# 1. Initialize EasyOCR and load the YOLO model
reader = easyocr.Reader(['en'])
model = YOLO("license_plate.pt")

# 2. Load the test image
image_path = "test_Image.jpeg"
image = cv2.imread(image_path)

# 3. Run YOLO detection
results = model(image)

# 4. Extract text from detected plates
for result in results:
    for box in result.boxes:
        # Get bounding box coordinates
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        
        # Crop the license plate region from the image
        plate_region = image[y1:y2, x1:x2]
        
        # Read text using EasyOCR
        ocr_result = reader.readtext(plate_region)
        
        # Print the text to the terminal
        for (_, text, confidence) in ocr_result:
            print(f"Detected Plate Text: {text} | Confidence: {confidence:.2f}")
            
        # Draw a box and text on the original image
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        if ocr_result:
            cv2.putText(image, ocr_result[0][1], (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

# 5. Save the annotated image
cv2.imwrite("ocr_result.jpeg", image)
print("Processing complete. Annotated image saved as ocr_result.jpeg")
