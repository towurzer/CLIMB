-- Correct pgvector's function cost estimates so the planner will actually choose the ANN index.

ALTER FUNCTION binary_quantize(halfvec) COST 170;
ALTER FUNCTION binary_quantize(vector) COST 170;

ALTER FUNCTION hamming_distance(bit, bit) COST 15;
ALTER FUNCTION jaccard_distance(bit, bit) COST 15;

ALTER FUNCTION cosine_distance(halfvec, halfvec) COST 70;
ALTER FUNCTION cosine_distance(vector, vector) COST 70;

ALTER FUNCTION l2_distance(halfvec, halfvec) COST 70;
ALTER FUNCTION l2_distance(vector, vector) COST 70;

ALTER FUNCTION inner_product(halfvec, halfvec) COST 70;
ALTER FUNCTION inner_product(vector, vector) COST 70;
