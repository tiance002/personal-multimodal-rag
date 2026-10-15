// Source: Tencent/WeKnora@3e8b0bfc (MIT). Extracted bodies with type/repository adapters.

package main
import("context";"encoding/json";"os";"errors")
type Chunk struct { ID string `json:"id"`; ParentChunkID string `json:"parent_chunk_id"`; ChunkType string `json:"chunk_type"`; ImageInfo string `json:"image_info"`; IsEnabled bool `json:"is_enabled"` }
const(ChunkTypeImageOCR="image_ocr";ChunkTypeImageCaption="image_caption";ChunkTypeText="text")
type SearchResult struct {ID string `json:"id"`; ImageInfo string `json:"image_info"`}
type Case struct {Name string `json:"name"`; Children []*Chunk `json:"children"`; Results []*SearchResult `json:"results"`; FailAt int `json:"fail_at"`}
type fixtureRepo struct {Rows []*Chunk; Calls *[][]string; FailAt int}
func(r fixtureRepo) list(ids []string)([]*Chunk,error){*r.Calls=append(*r.Calls,append([]string{},ids...));if len(*r.Calls)==r.FailAt{return nil,errors.New("fixture failure")};var rows []*Chunk;for _,row:=range r.Rows{for _,id:=range ids{if row.ParentChunkID==id{rows=append(rows,row);break}}};return rows,nil}
func CollectImageInfoByChunkIDs(ctx context.Context,r fixtureRepo,tenant uint64,ids []string)map[string]string{return collectImageInfoByChunkIDs(ids,r.list)}
func main(){var cases []Case;if err:=json.NewDecoder(os.Stdin).Decode(&cases);err!=nil{panic(err)};out:=[]any{};for _,c:=range cases{calls:=[][]string{};r:=fixtureRepo{c.Children,&calls,c.FailAt};EnrichSearchResultsImageInfo(context.Background(),r,1,c.Results);out=append(out,map[string]any{"name":c.Name,"results":c.Results,"calls":calls})};json.NewEncoder(os.Stdout).Encode(out)}

type ImageInfo struct {
	// 图片URL（COS）
	URL string `json:"url"          gorm:"type:text"`
	// 原始图片URL
	OriginalURL string `json:"original_url" gorm:"type:text"`
	// 图片在文本中的开始位置
	StartPos int `json:"start_pos"`
	// 图片在文本中的结束位置
	EndPos int `json:"end_pos"`
	// 图片描述
	Caption string `json:"caption"`
	// 图片OCR文本
	OCRText string `json:"ocr_text"`
}
func collectImageInfoByChunkIDs(
	chunkIDs []string,
	listChildren func(parentIDs []string) ([]*Chunk, error),
) map[string]string {
	if len(chunkIDs) == 0 {
		return nil
	}

	children, err := listChildren(chunkIDs)
	if err != nil || len(children) == 0 {
		return nil
	}

	type imageAgg struct {
		byURL map[string]ImageInfo
	}
	aggMap := make(map[string]*imageAgg)

	addInfo := func(targetID string, child *Chunk) {
		if child.ImageInfo == "" {
			return
		}
		var infos []ImageInfo
		if err := json.Unmarshal([]byte(child.ImageInfo), &infos); err != nil || len(infos) == 0 {
			return
		}
		agg, ok := aggMap[targetID]
		if !ok {
			agg = &imageAgg{byURL: make(map[string]ImageInfo)}
			aggMap[targetID] = agg
		}
		for _, info := range infos {
			key := info.URL
			if key == "" {
				key = info.OriginalURL
			}
			if key == "" {
				continue
			}
			existing, exists := agg.byURL[key]
			if !exists {
				agg.byURL[key] = info
			} else {
				if info.OCRText != "" {
					existing.OCRText = info.OCRText
				}
				if info.Caption != "" {
					existing.Caption = info.Caption
				}
				agg.byURL[key] = existing
			}
		}
	}

	var textChildIDs []string
	textToParent := make(map[string]string)

	for _, child := range children {
		if !child.IsEnabled {
			continue
		}
		switch child.ChunkType {
		case ChunkTypeImageOCR, ChunkTypeImageCaption:
			addInfo(child.ParentChunkID, child)
		case ChunkTypeText:
			textChildIDs = append(textChildIDs, child.ID)
			textToParent[child.ID] = child.ParentChunkID
		}
	}

	if len(textChildIDs) > 0 {
		grandChildren, err := listChildren(textChildIDs)
		if err == nil {
			for _, gc := range grandChildren {
				if !gc.IsEnabled {
					continue
				}
				if gc.ChunkType != ChunkTypeImageOCR && gc.ChunkType != ChunkTypeImageCaption {
					continue
				}
				if parentTextID, ok := textToParent[gc.ParentChunkID]; ok {
					addInfo(parentTextID, gc)
				}
			}
		}
	}

	out := make(map[string]string, len(aggMap))
	for id, agg := range aggMap {
		if len(agg.byURL) == 0 {
			continue
		}
		merged := make([]ImageInfo, 0, len(agg.byURL))
		for _, info := range agg.byURL {
			merged = append(merged, info)
		}
		data, err := json.Marshal(merged)
		if err != nil {
			continue
		}
		out[id] = string(data)
	}
	return out
}
func EnrichSearchResultsImageInfo(
	ctx context.Context,
	chunkRepo fixtureRepo,
	tenantID uint64,
	results []*SearchResult,
) {
	var chunkIDs []string
	seen := make(map[string]bool)
	for _, r := range results {
		if r.ImageInfo != "" {
			continue
		}
		if !seen[r.ID] {
			seen[r.ID] = true
			chunkIDs = append(chunkIDs, r.ID)
		}
	}
	if len(chunkIDs) == 0 {
		return
	}

	infoMap := CollectImageInfoByChunkIDs(ctx, chunkRepo, tenantID, chunkIDs)
	if len(infoMap) == 0 {
		return
	}

	for _, r := range results {
		if r.ImageInfo != "" {
			continue
		}
		if merged, ok := infoMap[r.ID]; ok {
			r.ImageInfo = merged
		}
	}
}
