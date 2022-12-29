# Trabalho Prático Análise Matemática I  
<sub> Martim Valente nº 2022128669 - LEEC </sub>


## Descrição do Objeto
Um sólido de revolução é uma figura sólida obtida pela rotação de um plano de curva em torno de alguma linha reta (o eixo), que se situa no mesmo plano.

![Imagem do objeto](https://i.ibb.co/RhM0Vyp/43acf46c-be94-4339-be1f-dcacd23a8480.jpg)

O objeto utilizado foi para o trabalho foi o cabo de uma chave de roquete antiga. Escolhi este objeto pela relativa simplicidade e por já estar familiarizado com ele, o que facilita o trabalho.

---

## Cálculo do Volume

Para começar, inseri a imagem do cabo no GeoGebra, e centrei de modo a haver simetria para poder projetar um eixo de rotação.

### Definição da Reta

De seguida marquei os pontos e defini o **`n`** de forma a ser o mais aproximado da forma do cabo, ou seja *c:

![Pontos no GeoGebra](https://cdn.discordapp.com/attachments/1057715137840173167/1057780978862526515/image.png)

``` 
f(x)=FitPoly({C,D,E,F,G,H,I,J,K,L,M,N,O,P},n)
```

### Definição do Limite

Depois disso defini os limites **`C`** a **`P`** (embora a linha esteja algo desfasada dos pontos marcados, esta é a melhor aproximação que reflete a forma do objeto)

![Pontos com o Limite](https://media.discordapp.net/attachments/1057715137840173167/1057781777890025502/image.png?width=1431&height=727)

```
Function(f,x(C),x(P))
g(x)=If(x(C)≤x≤x(P), f(x))

```

### Rotação do Objeto

De seguida usei a função **`Rotate(g,q,xAxis)`** que pega na funcão g definida anteriormente, num ângulo q a determinar e no eixo das abcissas.

```
g(x)=If(x(C)≤x≤x(P), f(x))
```
![Vídeo da rotação](https://cdn.discordapp.com/attachments/1057715137840173167/1057785288719613992/gifgeogebra.gif)

### Área do Objeto

A função **`Surface(g,q,xAxis)`** cria área:

![Objeto com area](https://cdn.discordapp.com/attachments/1057715137840173167/1057790868016353311/image.png)

```
Surface(g,q,xAxis)
```

Empurrando o slider para 360º / 2pi obtém-se o sólido por meio da rotação

``` 
a = Surface(g,q,xAxis) = 
(u . If (0.72 < u < 31.46,0 u4 + 0.01 u3 — 0.14 u2 + 0.9 u + 2.98) cos(v) 
If 
(0.72 < u < 31.46,0 u4 + 0.01 u3 — 0.14 u2 + 0.9 u + 2.98) sin(v) 
```

![Sólido Completo](https://media.discordapp.net/attachments/1057715137840173167/1057792969060659220/image.png?width=1431&height=727)

> Obtém-se assim o sólido de revolução

---

## Cálculos 

### Representar Curvas Gráficamente

#### Àrea
 
 ```
 Integral(g,x(C),x(P)) = 161.71
 ```
#### Volume
```
Integral(g,x(C),x(P))^(2) π = 82256.68
```
#### Perímetro
 ```
 Integral(sqrt(1+(Derivative(g)^((2)))),x(C),x(P)) = 34.39
 ```

![Cálculos Geogebra](https://cdn.discordapp.com/attachments/1057715137840173167/1057801887413436456/image.png)

## Bibliografia
 1. https://pt.wikipedia.org/wiki/S%C3%B3lido_de_revolu%C3%A7%C3%A3o
 2. https://www.geogebra.org/m/DmGH8WmK
 3. https://www.youtube.com/watch?v=euHh-ClVSrk
 4. https://www.youtube.com/watch?v=QLHJl2_aM5Q
 5. https://play.google.com/store/apps/details?id=com.google.ar.core&pli=1